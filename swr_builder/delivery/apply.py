"""用 Server-Side Apply 下发对象清单（fieldManager=swr-builder）。

- 平台只声明自己管理的字段；CCE 或其他工具写入、平台没有声明的字段保持不变。
- 字段被其他管理者占用时强制接管（force=true）：平台创建的对象与用户确认导入的对象都由平台负责。
- 下发前对全部对象执行一次 dryRun=All，任何对象校验失败都不写入集群。
- 共享配置（多个服务挂载的公共配置、证书目录）只升级不降级：线上已是同一配置集的更高版本时跳过。
- 开启 HPA 后平台不再声明 replicas；此前由平台管理的 replicas 先移交给单独的字段管理者，避免副本数被重置为 1。
- 上一次发布有、本次没有的服务对象（例如关闭了 Ingress 或 HPA）在确认带有平台标签后删除。
"""
from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote

import httpx

from ..deploy.k8s_client import KubeClient
from ..errors import DomainError
from .render import CONFIG_SET_LABEL, CONFIG_VERSION_ANNOTATION, MANAGED_BY, MANAGED_BY_LABEL, SERVICE_LABEL
from .spec import parse_quantity

FIELD_MANAGER = "swr-builder"
HANDOVER_MANAGER = "swr-builder-handover"
RESOURCES = {"ConfigMap": ("/api/v1", "configmaps"), "Secret": ("/api/v1", "secrets"), "Service": ("/api/v1", "services"),
             "Deployment": ("/apis/apps/v1", "deployments"),
             "Ingress": ("/apis/networking.k8s.io/v1", "ingresses"),
             "HorizontalPodAutoscaler": ("/apis/autoscaling/v2", "horizontalpodautoscalers")}
LIST_KEYS = ("name", "mountPath", "containerPort", "port", "topologyKey", "key")
PRUNABLE = ("Service", "Ingress", "HorizontalPodAutoscaler", "ConfigMap")


def object_path(namespace: str, kind: str, name: str) -> str:
    prefix, plural = RESOURCES[kind]
    return f"{prefix}/namespaces/{quote(namespace, safe='')}/{plural}/{quote(name, safe='')}"


def _short(value: Any, limit: int = 160) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
    return text if len(text) <= limit else text[:limit] + "…"


def _same(desired: Any, live: Any, path: str) -> bool:
    if desired == live:
        return True
    if isinstance(desired, (int, str)) and isinstance(live, (int, str)) and str(desired) == str(live):
        return True
    if ".resources." in path and isinstance(desired, str) and isinstance(live, str):
        try:
            return parse_quantity(desired) == parse_quantity(live)
        except ValueError:
            return False
    return False


def field_diff(desired: Any, live: Any, path: str = "", secret: str = "") -> List[Dict[str, Any]]:
    """平台声明的字段与线上的差异（只比较平台声明的字段）。

    secret="values"：Secret 的值只报告是否变化；secret="keys"：值是占位内容（预览不解密），只比较键是否存在。"""
    if isinstance(desired, dict):
        if not isinstance(live, dict):
            return [{"path": path or "/", "live": _short(live), "desired": "（对象）"}]
        rows: List[Dict[str, Any]] = []
        for key, value in desired.items():
            child = f"{path}.{key}" if path else key
            if secret and path == "data":
                if key not in live or (secret == "values" and live.get(key) != value):
                    rows.append({"path": child, "live": "（已隐藏）" if key in live else None, "desired": "（已隐藏）"})
                continue
            rows += field_diff(value, live.get(key), child, secret)
        return rows
    if isinstance(desired, list):
        if not isinstance(live, list):
            return [{"path": path, "live": _short(live), "desired": _short(desired)}]
        key = next((k for k in LIST_KEYS if desired and all(isinstance(i, dict) and k in i for i in desired)), None)
        if key is None:
            return [] if desired == live else [{"path": path, "live": _short(live), "desired": _short(desired)}]
        index = {item.get(key): item for item in live if isinstance(item, dict)}
        rows = []
        for item in desired:
            rows += field_diff(item, index.get(item[key]), f"{path}[{key}={item[key]}]", secret)
        return rows
    return [] if _same(desired, live, path) else [{"path": path, "live": _short(live), "desired": _short(desired)}]


def _status_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
        message = payload.get("message") if isinstance(payload, dict) else ""
    except ValueError:
        message = ""
    return f"HTTP {response.status_code}" + (f"：{message[:500]}" if message else "")


def _owns_replicas(live: Dict[str, Any]) -> bool:
    for entry in (live.get("metadata") or {}).get("managedFields") or []:
        if entry.get("manager") == FIELD_MANAGER and entry.get("operation") == "Apply":
            if "f:replicas" in ((entry.get("fieldsV1") or {}).get("f:spec") or {}):
                return True
    return False


def _live_config_version(live: Optional[Dict[str, Any]], desired: Dict[str, Any]) -> Optional[int]:
    """线上共享配置是同一个配置集时返回其版本号。"""
    if not live:
        return None
    labels = (live.get("metadata") or {}).get("labels") or {}
    wanted = (desired.get("metadata") or {}).get("labels", {}).get(CONFIG_SET_LABEL)
    if not wanted or labels.get(CONFIG_SET_LABEL) != wanted:
        return None
    value = ((live.get("metadata") or {}).get("annotations") or {}).get(CONFIG_VERSION_ANNOTATION, "")
    return int(value) if value.isdigit() else None


async def apply_objects(kube: KubeClient, namespace: str, objects: List[Dict[str, Any]], *, dry_run: bool,
                        previous: Iterable[Tuple[str, str]] = (), service_id: Optional[int] = None,
                        log: Any = None, secret_values: bool = True) -> List[Dict[str, Any]]:
    """逐个下发（或 dry-run）对象，返回每个对象的结果：action 为 create / update / unchanged / skip / delete / error。

    secret_values=False 表示 Secret 中是占位内容（预览），差异只比较键。"""
    results: List[Dict[str, Any]] = []
    for obj in objects:
        kind, name = obj["kind"], obj["metadata"]["name"]
        path = object_path(namespace, kind, name)
        row: Dict[str, Any] = {"kind": kind, "name": name, "action": "", "message": "", "diff": []}
        results.append(row)
        try:
            live = await kube.get_json(path)
        except DomainError as exc:
            row.update(action="error", message=str(exc))
            _log(log, dry_run, row)
            continue
        shared_version = _live_config_version(live, obj)
        desired_version = ((obj["metadata"].get("annotations") or {}).get(CONFIG_VERSION_ANNOTATION) or "")
        if shared_version is not None and desired_version.isdigit() and shared_version > int(desired_version):
            row.update(action="skip", message=f"线上已是该配置的 v{shared_version}，高于本次的 v{desired_version}，保持不变")
            _log(log, dry_run, row)
            continue
        mode = ("values" if secret_values else "keys") if kind == "Secret" else ""
        row["diff"] = field_diff(obj, live, secret=mode) if live else []
        if kind == "Deployment" and live and "replicas" not in obj["spec"] and _owns_replicas(live):
            row["message"] = "副本数交由 HPA 管理"
            if not dry_run:
                handover = {"apiVersion": obj["apiVersion"], "kind": kind, "metadata": {"name": name, "namespace": namespace},
                            "spec": {"replicas": (live.get("spec") or {}).get("replicas", 1)}}
                response = await kube.apply(path, handover, field_manager=HANDOVER_MANAGER, force=True)
                if response.status_code not in (200, 201):
                    row.update(action="error", message="移交副本数失败，" + _status_message(response))
                    _log(log, dry_run, row)
                    continue
        try:
            response = await kube.apply(path, obj, field_manager=FIELD_MANAGER, force=True, dry_run=dry_run)
        except httpx.HTTPError:
            row.update(action="error", message="连接集群失败")
            _log(log, dry_run, row)
            continue
        if response.status_code not in (200, 201):
            row.update(action="error", message=_status_message(response))
            _log(log, dry_run, row)
            continue
        try:
            result = response.json()
        except ValueError:
            result = {}
        row["uid"] = (result.get("metadata") or {}).get("uid", "")
        if live is None:
            row["action"] = "create"
        elif not dry_run and (live.get("metadata") or {}).get("resourceVersion") == \
                (result.get("metadata") or {}).get("resourceVersion"):
            row["action"] = "unchanged"
        else:
            row["action"] = "update" if row["diff"] or not dry_run else "unchanged"
        _log(log, dry_run, row)
    current = {(o["kind"], o["metadata"]["name"]) for o in objects}
    for kind, name in previous:
        if (kind, name) in current or kind not in PRUNABLE:
            continue
        path = object_path(namespace, kind, name)
        row = {"kind": kind, "name": name, "action": "delete", "message": "", "diff": []}
        try:
            live = await kube.get_json(path)
        except DomainError as exc:
            row.update(action="error", message=str(exc))
            results.append(row)
            continue
        labels = ((live or {}).get("metadata") or {}).get("labels") or {}
        if not live or labels.get(MANAGED_BY_LABEL) != MANAGED_BY or labels.get(SERVICE_LABEL) != str(service_id):
            continue
        response = await kube.delete(path, dry_run=dry_run)
        if response.status_code not in (200, 202, 404):
            row.update(action="error", message=_status_message(response))
        else:
            row["message"] = "本次清单中已不包含该对象"
        results.append(row)
        _log(log, dry_run, row)
    return results


def _log(log: Any, dry_run: bool, row: Dict[str, Any]) -> None:
    if log is None:
        return
    line = f"{'[dry-run] ' if dry_run else ''}{row['kind']}/{row['name']}：{row['action']} {row['message']}".rstrip()
    if row["action"] == "error":
        log.error(line)
    else:
        log.info(line)


def failures(results: List[Dict[str, Any]]) -> List[str]:
    return [f"{r['kind']}/{r['name']}：{r['message']}" for r in results if r["action"] == "error"]
