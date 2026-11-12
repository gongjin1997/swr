"""导入已有服务：分析命名空间的导出（kubectl get … -o yaml）或集群中的对象，给出托管方案。

纯函数，不访问数据库和集群。输出：
- 环境档案：对象命名规则、主机名规则、节点池调度、镜像拉取密钥、日志目录、资源与副本默认值（取多数服务的做法）；
- 负载均衡入口：Ingress 类、ELB 类型 / ID / 端口、证书 ID（取多数），与多数不一致的 Ingress 列为问题；
- 环境层配置：所有服务取值都相同的环境变量与敏感变量（至少两个服务时才上提）；
- 共享配置：被挂载的公共 ConfigMap / Secret，作为环境层的配置文件 / 敏感文件；
- 每个服务的托管规格与服务层配置，以及模板不支持、接管后会变化的地方；
- 资源推断：从 Deployment 和 ConfigMap 推断需要的资源类型（GaussDB、Redis、Kafka 等）。

敏感值只在内部以 bytes 传递；接口响应由 services/imports.py 生成，不含敏感值。
"""
from __future__ import annotations

import base64
import binascii
import posixpath
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

import yaml

from ..core.resource_inference import analyze_namespace_yaml
from .spec import DEFAULT_SPEC, DOWNWARD_ENV, PROBE_TIMINGS, SECURITY_CONTEXT, default_spec

MAX_YAML_BYTES = 20 * 1024 * 1024
KINDS = ("Deployment", "Service", "Ingress", "ConfigMap", "Secret", "HorizontalPodAutoscaler", "Pod")
SYSTEM_CONFIGMAPS = {"kube-root-ca.crt"}
SYSTEM_SECRET_TYPES = {"kubernetes.io/service-account-token", "kubernetes.io/dockerconfigjson", "kubernetes.io/dockercfg",
                       "cfe/secure-opaque", "helm.sh/release.v1", "bootstrap.kubernetes.io/token"}
CCE_INGRESS_ANNOTATIONS = {"kubernetes.io/elb.listener-master-ingress", "kubernetes.io/elb.inconsistent-listener-config"}
ELB_ANNOTATIONS = {"kubernetes.io/elb.class", "kubernetes.io/elb.id", "kubernetes.io/elb.port",
                   "kubernetes.io/elb.tls-certificate-ids"}
IGNORED_POD_ANNOTATIONS = {"redeploy-timestamp", "kubectl.kubernetes.io/restartedAt"}
URL_MATCH_PROPERTY = "ingress.beta.kubernetes.io/url-match-mode"
DEFAULT_NAMING = {"deployment": "{base}-app", "service": "{base}-service", "ingress": "{base}-ingress",
                  "configmap": "{base}-configmap", "secret": "{base}-secrets", "properties": "{base}-properties"}


class _NoAliasLoader(yaml.SafeLoader):
    """拒绝 YAML 锚点与别名（kubectl 输出不会用到，避免"十亿笑声"式的展开攻击）。"""

    def compose_node(self, parent, index):  # type: ignore[override]
        if self.check_event(yaml.AliasEvent):
            raise yaml.YAMLError("不支持 YAML 别名")
        return super().compose_node(parent, index)


def parse_documents(text: str) -> List[Dict[str, Any]]:
    """多文档 YAML 或 JSON；`kind: List` 展开为其中的 items。"""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("请粘贴或上传 kubectl 导出的 YAML")
    if len(text.encode("utf-8")) > MAX_YAML_BYTES:
        raise ValueError("导出内容超过 20 MiB，请按命名空间分别导入")
    objects: List[Dict[str, Any]] = []
    try:
        for doc in yaml.load_all(text, Loader=_NoAliasLoader):
            if doc is None:
                continue
            if not isinstance(doc, dict):
                raise ValueError("YAML 文档必须是 Kubernetes 对象")
            if str(doc.get("kind", "")).endswith("List") and isinstance(doc.get("items"), list):
                objects.extend(item for item in doc["items"] if isinstance(item, dict))
            else:
                objects.append(doc)
    except yaml.YAMLError as exc:
        raise ValueError(f"YAML 解析失败：{str(exc)[:300]}") from None
    return objects


def _meta(obj: Dict[str, Any]) -> Dict[str, Any]:
    return obj.get("metadata") or {}


def _name(obj: Dict[str, Any]) -> str:
    return _meta(obj).get("name", "")


def _majority(values: List[Any], default: Any) -> Any:
    values = [v for v in values if v is not None]
    if not values:
        return default
    counts = Counter(repr(v) for v in values)
    best = counts.most_common(1)[0][0]
    return next(v for v in values if repr(v) == best)


def _pattern(name: str, base: str, namespace: str) -> Optional[str]:
    if name.startswith(base):
        return "{base}" + name[len(base):].replace(namespace, "{namespace}")
    if namespace and namespace in name:
        return name.replace(namespace, "{namespace}")
    return None


def _decode(value: Any) -> bytes:
    if not isinstance(value, str):
        return b""
    try:
        return base64.b64decode(value, validate=False)
    except (binascii.Error, ValueError):
        return b""


def _config_data(obj: Dict[str, Any]) -> Dict[str, bytes]:
    """ConfigMap 的 data（文本）或 Secret 的 data（Base64 解码后）/ stringData。"""
    if obj.get("kind") == "Secret":
        data = {k: _decode(v) for k, v in (obj.get("data") or {}).items()}
        data.update({k: str(v).encode("utf-8") for k, v in (obj.get("stringData") or {}).items()})
        return data
    return {k: ("" if v is None else str(v)).encode("utf-8") for k, v in (obj.get("data") or {}).items()}


def _valid_profile(profile: Dict[str, Any], warnings: List[str]) -> Dict[str, Any]:
    """推断出的环境档案按平台规则校验；个别部分不合法时改用默认值并提示。"""
    from ..services.environments import DEFAULT_PROFILE, normalize_profile

    try:
        return normalize_profile(profile)
    except ValueError as exc:
        warnings.append(f"推断的环境档案不完全符合平台规则（{exc}），相关部分使用默认值")
    for key in ("naming", "host", "scheduling", "log", "defaults", "image_pull_secret"):
        try:
            return normalize_profile({**profile, key: DEFAULT_PROFILE[key]})
        except ValueError:
            continue
    return normalize_profile({})


def _probe_of(container: Dict[str, Any], warnings: List[str]) -> Dict[str, Any]:
    probe = dict(DEFAULT_SPEC["probe"])
    readiness = container.get("readinessProbe") or container.get("livenessProbe") or container.get("startupProbe")
    if not readiness:
        warnings.append("线上没有健康检查；接管后按模板增加启动、就绪、存活检查（可在服务配置中改为不检查）")
        return probe
    if readiness.get("httpGet"):
        get = readiness["httpGet"]
        port = get.get("port")
        if not isinstance(port, int):
            warnings.append(f"健康检查使用命名端口 {port}，模板需要端口号；请在导入后核对")
            port = probe["port"]
        probe.update(type="http", scheme=get.get("scheme", "HTTP"), path=get.get("path", "/"), port=port)
    elif readiness.get("tcpSocket") and isinstance(readiness["tcpSocket"].get("port"), int):
        probe.update(type="tcp", port=readiness["tcpSocket"]["port"])
    else:
        warnings.append("健康检查不是 HTTP 或 TCP 方式，模板不支持；接管后按模板的 HTTP 检查下发，请核对")
    different = []
    for kind, timings in PROBE_TIMINGS.items():
        live = container.get(kind)
        if not live:
            different.append(f"{kind} 线上没有")
            continue
        changed = [f"{k}={live.get(k)}" for k, v in timings.items() if live.get(k, {"successThreshold": 1}.get(k)) != v]
        if live.get("initialDelaySeconds"):
            changed.append(f"initialDelaySeconds={live['initialDelaySeconds']}")
        if changed:
            different.append(f"{kind}（{', '.join(changed)}）")
    if different:
        warnings.append("健康检查的时间参数与模板不同，接管后按模板下发：" + "；".join(different))
    return probe


class _Index:
    def __init__(self, objects: List[Dict[str, Any]]):
        self.by_kind: Dict[str, List[Dict[str, Any]]] = {kind: [] for kind in KINDS}
        self.ignored: Counter = Counter()
        for obj in objects:
            kind = obj.get("kind")
            if kind in self.by_kind:
                self.by_kind[kind].append(obj)
            elif kind:
                self.ignored[kind] += 1
        self.configmaps = {_name(o): o for o in self.by_kind["ConfigMap"]}
        self.secrets = {_name(o): o for o in self.by_kind["Secret"]}

    def services_for(self, labels: Dict[str, str]) -> List[Dict[str, Any]]:
        return [s for s in self.by_kind["Service"] if (s.get("spec") or {}).get("selector")
                and all(labels.get(k) == v for k, v in s["spec"]["selector"].items())]

    def ingresses_for(self, service_names: List[str]) -> List[Dict[str, Any]]:
        result = []
        for ingress in self.by_kind["Ingress"]:
            backends = {((p.get("backend") or {}).get("service") or {}).get("name")
                        for r in (ingress.get("spec") or {}).get("rules") or []
                        for p in (r.get("http") or {}).get("paths") or []}
            if backends & set(service_names):
                result.append(ingress)
        return result

    def hpa_for(self, deployment: str) -> Optional[Dict[str, Any]]:
        return next((h for h in self.by_kind["HorizontalPodAutoscaler"]
                     if ((h.get("spec") or {}).get("scaleTargetRef") or {}).get("kind") == "Deployment"
                     and h["spec"]["scaleTargetRef"].get("name") == deployment), None)

    def digests_for(self, labels: Dict[str, str], container: str) -> List[str]:
        digests = set()
        for pod in self.by_kind["Pod"]:
            pod_labels = _meta(pod).get("labels") or {}
            if labels and all(pod_labels.get(k) == v for k, v in labels.items()):
                for status in (pod.get("status") or {}).get("containerStatuses") or []:
                    image_id = status.get("imageID") or ""
                    if status.get("name") == container and "@sha256:" in image_id:
                        digests.add("sha256:" + image_id.rsplit("@sha256:", 1)[1])
        return sorted(digests)


def _volume_refs(deployment: Dict[str, Any]) -> List[Tuple[str, str]]:
    """Deployment 引用的 (kind, name)：envFrom、卷。"""
    pod = ((deployment.get("spec") or {}).get("template") or {}).get("spec") or {}
    refs = []
    for container in pod.get("containers") or []:
        for source in container.get("envFrom") or []:
            if source.get("configMapRef"):
                refs.append(("ConfigMap", source["configMapRef"].get("name", "")))
            if source.get("secretRef"):
                refs.append(("Secret", source["secretRef"].get("name", "")))
    for volume in pod.get("volumes") or []:
        if volume.get("configMap"):
            refs.append(("ConfigMap", volume["configMap"].get("name", "")))
        if volume.get("secret"):
            refs.append(("Secret", volume["secret"].get("secretName", "")))
    return refs


def analyze(objects: List[Dict[str, Any]], namespace: str) -> Dict[str, Any]:
    index = _Index(objects)
    deployments = sorted(index.by_kind["Deployment"], key=_name)
    warnings: List[str] = []
    namespaces = Counter(_meta(o).get("namespace") for o in objects if _meta(o).get("namespace"))
    if namespaces and namespace not in namespaces:
        warnings.append(f"导出内容来自命名空间 {'、'.join(sorted(namespaces))}，与当前环境 {namespace} 不同；"
                        "对象名中的命名空间部分将按当前环境处理")
    source_namespace = namespaces.most_common(1)[0][0] if namespaces else namespace
    references: Dict[Tuple[str, str], List[str]] = {}
    for deployment in deployments:
        for ref in _volume_refs(deployment):
            references.setdefault(ref, []).append(_name(deployment))

    # ---------------------------------------------------------------- 每个 Deployment 的原始信息
    drafts: List[Dict[str, Any]] = []
    deploy_suffix = sum(1 for d in deployments if _name(d).endswith("-app")) * 2 >= max(len(deployments), 1)
    for deployment in deployments:
        name = _name(deployment)
        spec = deployment.get("spec") or {}
        template = spec.get("template") or {}
        pod = template.get("spec") or {}
        containers = pod.get("containers") or []
        draft: Dict[str, Any] = {"deployment": name, "warnings": [], "supported": True, "raw": deployment}
        drafts.append(draft)
        if len(containers) != 1:
            draft.update(supported=False, reason="模板只支持单容器的 Deployment")
            continue
        selector = spec.get("selector") or {}
        if selector.get("matchExpressions") or not selector.get("matchLabels"):
            draft.update(supported=False, reason="selector 使用了 matchExpressions，模板不支持")
            continue
        container = containers[0]
        base = name[:-4] if deploy_suffix and name.endswith("-app") else name
        labels = (template.get("metadata") or {}).get("labels") or {}
        services = index.services_for(labels)
        service = next((s for s in services if _name(s) == f"{base}-service"), services[0] if services else None)
        if len(services) > 1:
            draft["warnings"].append(f"多个 Service 选中该工作负载，只接管 {_name(service)}")
        ingresses = index.ingresses_for([_name(service)]) if service else []
        ingress = ingresses[0] if ingresses else None
        if len(ingresses) > 1:
            draft["warnings"].append(f"多个 Ingress 转发到该服务，只接管 {_name(ingress)}")
        env_cm = next((s["configMapRef"].get("name") for s in container.get("envFrom") or [] if s.get("configMapRef")), None)
        env_secret = next((s["secretRef"].get("name") for s in container.get("envFrom") or [] if s.get("secretRef")), None)
        extra_env_from = [s for s in container.get("envFrom") or [] if s.get("prefix")] + \
            [s for s in (container.get("envFrom") or [])[2:]]
        if extra_env_from or sum(1 for s in container.get("envFrom") or [] if s.get("configMapRef")) > 1 \
                or sum(1 for s in container.get("envFrom") or [] if s.get("secretRef")) > 1:
            draft["warnings"].append("envFrom 引用了多个 ConfigMap / Secret 或使用了 prefix；接管后只保留服务自己的一组")
        draft.update(base=base, labels=labels, selector=selector["matchLabels"], container=container, pod=pod,
                     service=service, ingress=ingress, hpa=index.hpa_for(name), env_cm=env_cm, env_secret=env_secret)

    supported = [d for d in drafts if d["supported"]]

    # ---------------------------------------------------------------- 命名规则（多数服务的做法）
    naming = dict(DEFAULT_NAMING)
    naming["deployment"] = "{base}-app" if deploy_suffix else "{base}"
    role_names = {
        "service": lambda d: _name(d["service"]) if d["service"] else None,
        "ingress": lambda d: _name(d["ingress"]) if d["ingress"] else None,
        "configmap": lambda d: d["env_cm"], "secret": lambda d: d["env_secret"],
    }
    for role, getter in role_names.items():
        patterns = [_pattern(getter(d), d["base"], source_namespace) for d in supported if getter(d)]
        naming[role] = _majority([p for p in patterns if p], naming[role])

    # ---------------------------------------------------------------- 挂载：服务自己的 properties 与共享配置
    shared: Dict[Tuple[str, str], List[str]] = {}
    for draft in supported:
        volumes = {v.get("name"): v for v in draft["pod"].get("volumes") or []}
        mounts: List[Dict[str, Any]] = []
        groups: Dict[str, Dict[str, Any]] = {}
        log = None
        for mount in draft["container"].get("volumeMounts") or []:
            volume = volumes.get(mount.get("name"))
            if not volume:
                continue
            if volume.get("hostPath"):
                log = {"volume": volume["name"], "host_path": volume["hostPath"].get("path", ""),
                       "mount_path": mount.get("mountPath", ""), "sub_path_expr": mount.get("subPathExpr", "")}
                continue
            source = volume.get("configMap") or volume.get("secret")
            if not source:
                draft["warnings"].append(f"卷 {volume.get('name')} 的类型模板不支持，接管后平台不管理该卷（保持现状）")
                continue
            if source.get("items"):
                draft["warnings"].append(f"卷 {volume['name']} 使用了 items 重命名文件，模板不支持；接管后按原文件名挂载")
            kind = "ConfigMap" if volume.get("configMap") else "Secret"
            object_name = source.get("name") or source.get("secretName") or ""
            group = groups.setdefault(volume["name"], {"kind": kind, "object": object_name, "volume": volume["name"],
                                                       "paths": set(), "items": [], "directory": None})
            if mount.get("subPath"):
                directory, filename = posixpath.split(mount["mountPath"].rstrip("/"))
                if filename != mount["subPath"]:
                    draft["warnings"].append(f"{mount['mountPath']} 挂载的文件名与 subPath 不同，模板不支持；请核对")
                group["paths"].add(directory or "/")
                group["items"].append(mount["subPath"])
            else:
                group["directory"] = mount.get("mountPath", "")
        for group in groups.values():
            if group["directory"] is not None and group["items"] or len(group["paths"]) > 1:
                draft["warnings"].append(f"卷 {group['volume']} 同时以目录和多个位置挂载，模板不支持；只接管第一处")
            path = group["directory"] if group["directory"] is not None else sorted(group["paths"])[0]
            users = references.get((group["kind"], group["object"]), [])
            own = group["kind"] == "ConfigMap" and len(users) == 1 and group["items"] and \
                not any(m.get("config") == "properties" for m in mounts)
            config = "properties" if own else group["object"]
            mounts.append({"config": config, "path": path, "items": group["items"] if group["directory"] is None else [],
                           "volume": group["volume"], "object": group["object"], "kind": group["kind"]})
            if not own:
                shared.setdefault((group["kind"], group["object"]), []).append(draft["deployment"])
        draft["mounts"] = mounts
        draft["log"] = log
    patterns = [_pattern(m["object"], d["base"], source_namespace) for d in supported for m in d["mounts"]
                if m["config"] == "properties"]
    naming["properties"] = _majority([p for p in patterns if p], naming["properties"])

    # ---------------------------------------------------------------- 环境档案
    def scheduling_of(pod: Dict[str, Any]) -> Dict[str, Any]:
        affinity = pod.get("affinity") or {}
        terms = (((affinity.get("nodeAffinity") or {}).get("requiredDuringSchedulingIgnoredDuringExecution") or {})
                 .get("nodeSelectorTerms") or [])
        expressions = [e for t in terms for e in t.get("matchExpressions") or []]
        pool = next(((e.get("key"), (e.get("values") or [""])[0]) for e in expressions
                     if e.get("operator") == "In" and len(e.get("values") or []) == 1), (None, None))
        anti = any(t.get("topologyKey") == "kubernetes.io/hostname" for t in
                   (affinity.get("podAntiAffinity") or {}).get("requiredDuringSchedulingIgnoredDuringExecution") or [])
        spread = any(c.get("topologyKey") == "topology.kubernetes.io/zone" for c in pod.get("topologySpreadConstraints") or [])
        return {"node_pool_label": pool[0], "node_pool": pool[1], "anti_affinity": anti, "zone_spread": spread}

    schedules = [scheduling_of(d["pod"]) for d in supported]
    scheduling = {
        "node_pool_label": _majority([s["node_pool_label"] for s in schedules], "cce.cloud.com/cce-nodepool"),
        "node_pool": _majority([s["node_pool"] for s in schedules], ""),
        "anti_affinity": _majority([s["anti_affinity"] for s in schedules], True),
        "zone_spread": _majority([s["zone_spread"] for s in schedules], True),
    }
    pull_secrets = [((d["pod"].get("imagePullSecrets") or [{}])[0]).get("name", "") for d in supported]
    logs = [d["log"] for d in supported if d["log"]]
    log_profile = {"enabled": bool(logs), "host_path": _majority([lg["host_path"] for lg in logs], ""),
                   "mount_path": _majority([lg["mount_path"] for lg in logs], "/var/log/app"),
                   "sub_path_expr": _majority([lg["sub_path_expr"] for lg in logs], "")}
    resources = [((d["container"].get("resources") or {})) for d in supported]
    defaults = {
        "replicas": _majority([(d["raw"].get("spec") or {}).get("replicas", 1) for d in supported if not d["hpa"]], 1),
        "cpu_request": str(_majority([(r.get("requests") or {}).get("cpu") for r in resources], "500m")),
        "memory_request": str(_majority([(r.get("requests") or {}).get("memory") for r in resources], "1Gi")),
        "cpu_limit": str(_majority([(r.get("limits") or {}).get("cpu") for r in resources], "1")),
        "memory_limit": str(_majority([(r.get("limits") or {}).get("memory") for r in resources], "1Gi")),
    }
    hosts = [_pattern(r.get("host", ""), d["base"], source_namespace)
             for d in supported if d["ingress"] for r in (d["ingress"].get("spec") or {}).get("rules") or [] if r.get("host")]
    profile = _valid_profile({"naming": naming, "host": _majority([h for h in hosts if h], "{base}-app.{namespace}"),
                              "scheduling": scheduling, "image_pull_secret": _majority(pull_secrets, "default-secret") or "",
                              "log": log_profile, "defaults": defaults}, warnings)

    # ---------------------------------------------------------------- 负载均衡入口
    def entry_of(ingress: Dict[str, Any]) -> Dict[str, Any]:
        annotations = _meta(ingress).get("annotations") or {}
        paths = [p for r in (ingress.get("spec") or {}).get("rules") or [] for p in (r.get("http") or {}).get("paths") or []]
        mode = next(((p.get("property") or {}).get(URL_MATCH_PROPERTY, "") for p in paths if p.get("property")), "")
        port = annotations.get("kubernetes.io/elb.port", "443")
        return {"ingress_class": (ingress.get("spec") or {}).get("ingressClassName", "cce"),
                "elb_class": annotations.get("kubernetes.io/elb.class", "performance"),
                "elb_id": annotations.get("kubernetes.io/elb.id", ""), "port": int(port) if str(port).isdigit() else 443,
                "certificate_ids": [c for c in annotations.get("kubernetes.io/elb.tls-certificate-ids", "").split(",") if c],
                "url_match_mode": mode,
                "annotations": {k: v for k, v in annotations.items()
                                if k not in ELB_ANNOTATIONS | CCE_INGRESS_ANNOTATIONS and not k.startswith("swr-builder/")}}

    entries = {d["deployment"]: entry_of(d["ingress"]) for d in supported if d["ingress"]}
    ingress_profile = _majority(list(entries.values()), None) if entries else None
    if ingress_profile:
        for deployment, entry in sorted(entries.items()):
            different = [label for key, label in (("ingress_class", "IngressClass"), ("elb_class", "ELB 类型"),
                                                  ("elb_id", "ELB 实例"), ("port", "监听端口"),
                                                  ("certificate_ids", "证书 ID"), ("annotations", "其他注解"))
                         if entry[key] != ingress_profile[key]]
            if different:
                warnings.append(f"{deployment} 的 Ingress 与多数服务的{'、'.join(different)}不同；"
                                "接管后统一使用环境的负载均衡入口")
        for draft in supported:
            annotations = _meta(draft["ingress"]).get("annotations") or {} if draft["ingress"] else {}
            if annotations.get("kubernetes.io/elb.inconsistent-listener-config"):
                warnings.append(f"CCE 已标记 {draft['deployment']} 的 Ingress 与共享监听器配置不一致"
                                f"（{annotations['kubernetes.io/elb.inconsistent-listener-config']}），它写的设置当前没有生效")

    # ---------------------------------------------------------------- 环境变量：上提所有服务都相同的键
    env_maps: Dict[str, Dict[str, bytes]] = {}
    secret_maps: Dict[str, Dict[str, bytes]] = {}
    for draft in supported:
        cm = index.configmaps.get(draft["env_cm"] or "")
        secret = index.secrets.get(draft["env_secret"] or "")
        if draft["env_cm"] and not cm:
            draft["warnings"].append(f"导出中没有环境变量 ConfigMap {draft['env_cm']}")
        if draft["env_secret"] and not secret:
            draft["warnings"].append(f"导出中没有敏感变量 Secret {draft['env_secret']}（导出时可能没有 Secret 的读取权限）")
        env_maps[draft["deployment"]] = _config_data(cm) if cm else {}
        secret_maps[draft["deployment"]] = _config_data(secret) if secret else {}
        for ref_kind, ref_name in (("ConfigMap", draft["env_cm"]), ("Secret", draft["env_secret"])):
            if ref_name and len(references.get((ref_kind, ref_name), [])) > 1:
                draft["warnings"].append(f"{ref_name} 被多个服务共用；接管后每个服务使用自己的对象（内容相同）")
                draft.setdefault("rename", []).append(ref_kind)

    def lift(maps: Dict[str, Dict[str, bytes]]) -> Dict[str, bytes]:
        if len(maps) < 2:
            return {}
        values = list(maps.values())
        common = set(values[0])
        for data in values[1:]:
            common &= set(data)
        return {k: values[0][k] for k in sorted(common) if all(data[k] == values[0][k] for data in values)}

    common_env, common_secret = lift(env_maps), lift(secret_maps)

    # ---------------------------------------------------------------- 共享配置文件
    files = []
    for (kind, object_name), users in sorted(shared.items()):
        obj = (index.configmaps if kind == "ConfigMap" else index.secrets).get(object_name)
        if not obj:
            warnings.append(f"导出中没有被挂载的{kind} {object_name}")
            continue
        if obj.get("binaryData"):
            warnings.append(f"ConfigMap {object_name} 含 binaryData，配置文件只支持文本；这些文件请改用敏感文件或证书库")
        files.append({"name": object_name, "kind": "files" if kind == "ConfigMap" else "secret_files",
                      "entries": _config_data(obj), "used_by": sorted(set(users))})
    used = {ref for ref in references} | {("ConfigMap", n) for d in supported for n in [d["env_cm"]] if n}
    unused = [{"kind": o.get("kind"), "name": _name(o)} for o in index.by_kind["ConfigMap"] + index.by_kind["Secret"]
              if (o.get("kind"), _name(o)) not in used and _name(o) not in SYSTEM_CONFIGMAPS
              and o.get("type") not in SYSTEM_SECRET_TYPES]
    for item in unused:
        warnings.append(f"{item['kind']} {item['name']} 没有被任何工作负载引用，不会导入；请确认是否遗漏")

    # ---------------------------------------------------------------- 每个服务的规格
    services = []
    for draft in drafts:
        if not draft["supported"]:
            services.append({"deployment": draft["deployment"], "supported": False, "reason": draft["reason"],
                             "warnings": [], "objects": [{"kind": "Deployment", "name": draft["deployment"]}]})
            continue
        services.append(_service_spec(draft, index, profile, ingress_profile, entries, common_env, common_secret,
                                      env_maps, secret_maps, source_namespace))
    # ---------------------------------------------------------------- 资源推断
    resource_analysis = analyze_namespace_yaml(objects)

    return {"source_namespace": source_namespace, "counts": {k: len(v) for k, v in index.by_kind.items()},
            "ignored": dict(index.ignored), "profile": profile, "ingress": ingress_profile,
            "env": common_env, "secret_env": common_secret, "files": files, "services": services,
            "resource_inferences": resource_analysis,
            "warnings": warnings, "unused": unused}


def _service_spec(draft: Dict[str, Any], index: _Index, profile: Dict[str, Any], ingress_profile: Optional[Dict[str, Any]],
                  entries: Dict[str, Dict[str, Any]], common_env: Dict[str, bytes], common_secret: Dict[str, bytes],
                  env_maps: Dict[str, Dict[str, bytes]], secret_maps: Dict[str, Dict[str, bytes]],
                  namespace: str) -> Dict[str, Any]:
    name, base, container, pod = draft["deployment"], draft["base"], draft["container"], draft["pod"]
    warnings: List[str] = list(draft["warnings"])
    raw_spec = draft["raw"].get("spec") or {}
    spec = default_spec(base=base)
    values = {"base": base, "service": base, "namespace": namespace}
    expected = {role: pattern.format(**values) for role, pattern in profile["naming"].items()}
    actual = {"deployment": name, "service": _name(draft["service"]) if draft["service"] else None,
              "ingress": _name(draft["ingress"]) if draft["ingress"] else None,
              "configmap": draft["env_cm"], "secret": draft["env_secret"],
              "properties": next((m["object"] for m in draft["mounts"] if m["config"] == "properties"), None)}
    for kind in draft.get("rename", []):
        actual["configmap" if kind == "ConfigMap" else "secret"] = None
    spec["names"] = {role: value for role, value in actual.items() if value and value != expected[role]}
    if draft["hpa"] and _name(draft["hpa"]) != name:
        spec["names"]["hpa"] = _name(draft["hpa"])
    spec["selector"] = dict(draft["selector"])
    env = [e for e in container.get("env") or []]
    downward = {n: p for n, p in DOWNWARD_ENV}
    present = {e["name"] for e in env if (((e.get("valueFrom") or {}).get("fieldRef") or {}).get("fieldPath")
                                          == downward.get(e.get("name")))}
    plain = [{"name": e["name"], "value": str(e.get("value", ""))} for e in env if "valueFrom" not in e]
    others = [e["name"] for e in env if "valueFrom" in e and e["name"] not in present]
    if others:
        warnings.append(f"环境变量 {'、'.join(others)} 使用 valueFrom 引用，模板不支持；接管后平台不管理这些变量（保持现状）")
    if present and len(present) < len(downward):
        warnings.append("线上只注入了部分 Downward API 变量；接管后注入全部 4 个（节点 IP、Pod 名、命名空间、Pod IP）")
    container_name = container.get("name", "")
    spec["container"] = {"name": container_name if container_name != name else "", "env": plain,
                         "downward_api": bool(present), "image_pull_policy": container.get("imagePullPolicy", "IfNotPresent")}
    spec["probe"] = _probe_of(container, warnings)
    resources = container.get("resources") or {}
    live = {"cpu_request": (resources.get("requests") or {}).get("cpu"),
            "memory_request": (resources.get("requests") or {}).get("memory"),
            "cpu_limit": (resources.get("limits") or {}).get("cpu"), "memory_limit": (resources.get("limits") or {}).get("memory")}
    spec["resources"] = {k: str(v) for k, v in live.items() if v is not None and str(v) != profile["defaults"][k]}
    missing = [k for k, v in live.items() if v is None]
    if missing:
        warnings.append(f"线上没有设置 {'、'.join(missing)}，接管后使用环境默认值")
    if not draft["hpa"] and raw_spec.get("replicas", 1) != profile["defaults"]["replicas"]:
        spec["replicas"] = raw_spec.get("replicas", 1)
    spec["workload"] = {"termination_grace_period_seconds": pod.get("terminationGracePeriodSeconds", 30)}
    security = container.get("securityContext") or {}
    if any(security.get(k) != v for k, v in SECURITY_CONTEXT.items()):
        warnings.append("容器的安全上下文与模板不同（非 root 65532、禁止提权），接管后按模板下发；请确认镜像可以以该用户运行")
    for field, label in (("command", "启动命令"), ("args", "启动参数"), ("lifecycle", "生命周期钩子"), ("ports", "容器端口")):
        if container.get(field):
            warnings.append(f"容器设置了{label}，模板不管理该字段（接管后保持现状）")
    for field, label in (("initContainers", "初始化容器"), ("tolerations", "污点容忍"), ("hostAliases", "hostAliases"),
                         ("dnsConfig", "DNS 配置"), ("nodeSelector", "nodeSelector")):
        if pod.get(field):
            warnings.append(f"Pod 设置了{label}，模板不管理该字段（接管后保持现状）")
    schedule = profile["scheduling"]
    affinity = pod.get("affinity") or {}
    lacks = [label for flag, present_now, label in (
        (bool(schedule["node_pool"]), bool(affinity.get("nodeAffinity")), "节点池亲和"),
        (schedule["anti_affinity"], bool(affinity.get("podAntiAffinity")), "同服务反亲和"),
        (schedule["zone_spread"], bool(pod.get("topologySpreadConstraints")), "跨可用区打散")) if flag and not present_now]
    if lacks:
        warnings.append(f"线上没有{'、'.join(lacks)}，接管后按环境档案补齐")
    service = draft["service"]
    if service:
        ports = []
        for port in (service.get("spec") or {}).get("ports") or []:
            item = {"name": port.get("name", ""), "port": port.get("port"), "target_port": port.get("targetPort", port.get("port"))}
            if port.get("nodePort") and (service["spec"].get("type") in ("NodePort", "LoadBalancer")):
                item["node_port"] = port["nodePort"]
            ports.append(item)
        spec["service"] = {"enabled": True, "type": service["spec"].get("type", "ClusterIP"), "ports": ports}
    else:
        spec["service"] = dict(DEFAULT_SPEC["service"], enabled=False)
    ingress = draft["ingress"]
    if ingress:
        rules = (ingress.get("spec") or {}).get("rules") or []
        paths = (rules[0].get("http") or {}).get("paths") or [] if rules else []
        if len(rules) > 1 or len(paths) > 1:
            warnings.append("Ingress 有多条规则或路径，模板只管理第一条")
        host = rules[0].get("host", "") if rules else ""
        expected_host = profile["host"].format(**values)
        path = paths[0] if paths else {}
        entry = entries.get(name) or {}
        spec["ingress"] = {"enabled": True, "host": "" if host == expected_host else host, "path": path.get("path", "/"),
                           "port": (((path.get("backend") or {}).get("service") or {}).get("port") or {}).get("number"),
                           "url_match_mode": entry.get("url_match_mode", "") if entry.get("url_match_mode") !=
                           (ingress_profile or {}).get("url_match_mode") else ""}
    hpa = draft["hpa"]
    if hpa:
        metrics = (hpa.get("spec") or {}).get("metrics") or []
        cpu = next((((m.get("resource") or {}).get("target") or {}).get("averageUtilization") for m in metrics
                    if (m.get("resource") or {}).get("name") == "cpu"), None)
        if cpu is None or len(metrics) > 1:
            warnings.append("HPA 使用了 CPU 利用率以外的指标，模板只管理 CPU 利用率")
        spec["hpa"] = {"enabled": True, "min_replicas": hpa["spec"].get("minReplicas", 1),
                       "max_replicas": hpa["spec"].get("maxReplicas", 1), "cpu_utilization": cpu or 70}
    spec["mounts"] = [{k: m[k] for k in ("config", "path", "items", "volume")} for m in draft["mounts"]]
    log = draft["log"]
    spec["log"] = {"enabled": bool(log), "volume": log["volume"] if log else "log"}
    if log and (log["host_path"], log["mount_path"], log["sub_path_expr"]) != (
            profile["log"]["host_path"], profile["log"]["mount_path"], profile["log"]["sub_path_expr"]):
        warnings.append("日志目录与多数服务不同，接管后使用环境档案中的日志目录")
    annotations = ((draft["raw"].get("spec") or {}).get("template") or {}).get("metadata", {}).get("annotations") or {}
    spec["pod_annotations"] = {k: str(v) for k, v in annotations.items()
                               if k not in IGNORED_POD_ANNOTATIONS and not k.startswith("swr-builder/")}
    env_data, secret_data = env_maps.get(name, {}), secret_maps.get(name, {})
    properties = next((m for m in draft["mounts"] if m["config"] == "properties"), None)
    files_data = _config_data(index.configmaps[properties["object"]]) if properties and properties["object"] in index.configmaps else {}
    objects = [{"kind": "Deployment", "name": name}]
    for kind, value in (("Service", actual["service"]), ("Ingress", actual["ingress"]),
                        ("ConfigMap", draft["env_cm"]), ("Secret", draft["env_secret"]),
                        ("ConfigMap", actual["properties"]), ("HorizontalPodAutoscaler", _name(hpa) if hpa else None)):
        if value:
            objects.append({"kind": kind, "name": value})
    containers = pod.get("containers") or [{}]
    return {
        "deployment": name, "name": base, "supported": True, "reason": "", "spec": spec,
        "image": containers[0].get("image", ""), "digests": index.digests_for(draft["labels"], container_name),
        "config": {"env": {k: v for k, v in env_data.items() if k not in common_env},
                   "secret_env": {k: v for k, v in secret_data.items() if k not in common_secret},
                   "files": files_data},
        "live_env": env_data, "live_secret_env": secret_data,
        "objects": objects, "warnings": warnings,
    }
