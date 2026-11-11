"""服务发布：提交（发布 / 回滚 / 重新发布）、幂等与策略、审批、发布记录。实际执行见 deploy/worker.py。

流程：选择服务所属应用的成功构建 + 配置修订 → 固定镜像 digest → 检查冻结与发布窗口 →
按命名空间策略进入"待审批"或"排队" → 审批人数达到要求后排队 → worker 写入 Kubernetes 前再次复核。
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, List, Optional, Tuple

from ..access import Principal, load_principal, role_index, same_user
from ..errors import Conflict, Forbidden, NotFound, NotImplementedYet, Unprocessable
from ..policy import deployment_policy_block
from ..registry import parse_image_ref
from ..store import Store
from ..validation import reject_unknown, text_field
from .catalog import Lookup, writable_service
from .namespaces import effective_policy, required_approvals

DEPLOYMENT_RESPONSE_FIELDS = ("id", "service_id", "job_id", "revision_id", "kind", "status", "digest", "created_at")
IDEMPOTENCY_KEY_RE = re.compile(r"[A-Za-z0-9._:-]{8,128}")
DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
FINAL_STATUSES = ("success", "failed", "cancelled", "rejected", "expired", "unknown")
KIND_PERMISSIONS = {"deploy": ("service.deploy", "没有服务发布权限"),
                    "rollback": ("service.rollback", "没有服务回滚权限"),
                    "restart": ("service.operate", "没有服务运维权限")}


def deployment_response(deployment: Dict[str, Any]) -> Dict[str, Any]:
    return {key: deployment.get(key) for key in DEPLOYMENT_RESPONSE_FIELDS}


def request_hash(kind: str, payload: Dict[str, Any]) -> str:
    raw = json.dumps({"kind": kind, "payload": payload}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ 审批规则
def requires_admin_approval(store: Store, deployment: Dict[str, Any],
                            namespace: Optional[Dict[str, Any]] = None) -> bool:
    """生产环境、命名空间策略或提交时的策略快照要求平台管理员确认。"""
    snapshot = json.loads(deployment.get("policy_snapshot") or "{}")
    if namespace is None and deployment.get("namespace_id"):
        namespace = store.get_namespace(deployment["namespace_id"])
    return bool(snapshot.get("require_admin_approval")) or bool(namespace and (
        namespace["tier"] == "prod" or namespace["policy"].get("require_admin_approval")))


def can_decide(store: Store, actor: Principal, deployment: Dict[str, Any], decision: str = "approved", *,
               admin_required: Optional[bool] = None) -> bool:
    """发起人不能审批自己的任务；需要管理员确认的批准只有平台管理员可以做。"""
    if same_user(actor.id, deployment.get("created_by")):
        return False
    if decision == "approved":
        if admin_required is None:
            admin_required = requires_admin_approval(store, deployment)
        if admin_required:
            return actor.is_platform_admin
    return actor.can_namespace(deployment.get("namespace_id"), "deploy.approve")


def valid_approval_count(store: Store, deployment: Dict[str, Any]) -> int:
    """仍然有效的批准数：审批人仍启用、无需改密，且现在仍有审批该任务的权限。"""
    count, roles = 0, role_index(store)
    admin_required = requires_admin_approval(store, deployment)
    for approval in store.list_approvals(deployment["id"]):
        actor = store.get_user(approval["approver_id"])
        if approval["decision"] != "approved" or not actor or actor["must_change_password"]:
            continue
        if can_decide(store, load_principal(store, actor, roles=roles), deployment, admin_required=admin_required):
            count += 1
    return count


# ------------------------------------------------------------------ 提交
def check_idempotency_key(key: str) -> None:
    if not IDEMPOTENCY_KEY_RE.fullmatch(key or ""):
        raise ValueError("Idempotency-Key 格式无效（8–128 位字母、数字或 . _ : -）")


def resolve_revision(store: Store, service: Dict[str, Any], revision_id: Optional[int] = None,
                     use_latest_config: bool = False) -> Dict[str, Any]:
    """未指定修订时：首次发布或 use_latest_config 用当前配置修订，否则沿用已部署的修订。"""
    selected = revision_id
    if selected is None:
        if use_latest_config or service["deployed_revision_id"] is None:
            selected = service["current_revision_id"]
        else:
            selected = service["deployed_revision_id"]
    revision = store.get_revision(service["id"], selected) if selected is not None else None
    if not revision:
        raise NotFound("服务修订不存在")
    return revision


def queue_deployment(store: Store, service: Dict[str, Any], job: Dict[str, Any], revision: Dict[str, Any],
                     actor: Dict[str, Any], kind: str, idempotency_key: str, remark: str = "") -> Dict[str, Any]:
    """创建发布任务。相同幂等键 + 相同请求返回已有任务；需要审批时状态为 pending_approval。"""
    if revision["spec"].get("mode") != "image_only":
        raise NotImplementedYet("managed 服务的清单渲染与 SSA 尚未开放")
    digest = job.get("digest")
    if not isinstance(digest, str) or not DIGEST_RE.fullmatch(digest):
        raise Unprocessable("成功构建记录缺少有效的 SHA-256 摘要")
    image_ref = parse_image_ref(job.get("image") or "")
    if not image_ref:
        raise Unprocessable("成功构建记录的镜像引用无效")
    namespace = store.get_namespace(service["namespace_id"])
    if not namespace:
        raise NotFound("服务所在的命名空间不存在")
    policy = effective_policy(namespace["policy"])
    status = "pending_approval" if required_approvals(policy, namespace["tier"]) else "queued"
    approval_policy = dict(namespace["policy"])
    approval_policy["require_admin_approval"] = (
        bool(approval_policy.get("require_admin_approval")) or namespace["tier"] == "prod")
    payload_hash = request_hash(kind, {"job_id": job["id"], "revision_id": revision["id"], "remark": remark})
    existing = store.find_deployment_idempotency(actor["id"], service["id"], idempotency_key)
    if existing:
        if existing["request_hash"] != payload_hash:
            raise Conflict("幂等键已用于不同的部署请求", code="IDEMPOTENCY_CONFLICT")
        return existing
    blocked = deployment_policy_block(policy)
    if blocked:
        raise Forbidden(blocked, code="POLICY_BLOCKED")
    return store.create_idempotent_service_deployment(
        target_id=None, target_name=service["name"], target_type="kubernetes", environment=namespace["tier"],
        app_id=service["app_id"], app_name=job["app_name"], job_id=job["id"],
        image=image_ref.host + "/" + image_ref.repo + "@" + digest,
        project_id=service["project_id"], service_id=service["id"], namespace_id=service["namespace_id"],
        revision_id=revision["id"], kind=kind, digest=digest, idempotency_key=idempotency_key,
        request_hash=payload_hash, policy_snapshot=json.dumps(approval_policy, ensure_ascii=False),
        status=status, trigger="manual", operator=actor.get("display_name") or actor["username"],
        created_by=actor["id"], remark=remark[:200])


def _bool(data: Dict[str, Any], key: str) -> bool:
    value = data.get(key, False)
    if not isinstance(value, bool):
        raise ValueError(f"{key} 必须是布尔值")
    return value


def submit(store: Store, me: Principal, service_id: int, kind: str, data: Any, idempotency_key: str
           ) -> Dict[str, Any]:
    """发布（deploy）、回滚（rollback）或以最近一次成功发布重新发布（restart）。"""
    if kind not in KIND_PERMISSIONS:
        raise ValueError("发布类型无效")
    if not isinstance(data, dict):
        raise ValueError("发布请求格式错误")
    check_idempotency_key(idempotency_key)
    allowed = {"deploy": {"job_id", "revision_id", "use_latest_config", "remark"},
               "rollback": {"target_deployment_id", "remark"}, "restart": {"remark"}}[kind]
    reject_unknown(data, allowed, "发布请求包含未知字段")
    service = writable_service(store, me, service_id, *KIND_PERMISSIONS[kind])
    remark = text_field(data, "remark", 200)
    if kind == "deploy":
        job_id = data.get("job_id")
        if type(job_id) is not int:
            raise ValueError("job_id 必须是整数")
        job = store.get_job(job_id)
        if (not job or job["status"] != "success" or job["app_id"] != service["app_id"]
                or job["project_id"] != service["project_id"]):
            raise Unprocessable("构建必须成功且属于服务对应的应用和项目")
        revision = resolve_revision(store, service, data.get("revision_id"), _bool(data, "use_latest_config"))
    else:
        if kind == "rollback":
            target_id = data.get("target_deployment_id")
            if type(target_id) is not int:
                raise ValueError("target_deployment_id 必须是整数")
            previous = store.get_deployment(target_id)
            if not previous or previous["service_id"] != service_id or previous["status"] != "success":
                raise NotFound("成功发布记录不存在")
        else:
            rows, _ = store.list_deployments(service_id=service_id, status="success", limit=1, offset=0)
            previous = rows[0] if rows else None
            if not previous:
                raise Conflict("服务还没有成功的发布记录")
        job = store.get_job(previous["job_id"])
        revision = store.get_revision(service_id, previous["revision_id"])
        if not job or not revision:
            raise Conflict("历史发布引用的构建或配置修订已不可用")
    return queue_deployment(store, service, job, revision, me.user, kind, idempotency_key, remark)


# ------------------------------------------------------------------ 审批
def decide(store: Store, deployment: Dict[str, Any], actor: Principal, decision: str, comment: str) -> Dict[str, Any]:
    """记录审批意见。拒绝立即结束任务；批准数达到策略快照中的 min_approvals 后进入发布队列。"""
    if deployment["status"] != "pending_approval":
        raise Conflict("发布任务不在待审批状态")
    admin_required = decision == "approved" and requires_admin_approval(store, deployment)
    if same_user(actor.id, deployment.get("created_by")):
        raise Forbidden("发布发起人不能审批自己的任务")
    if not can_decide(store, actor, deployment, decision, admin_required=admin_required):
        raise Forbidden("此发布需要平台管理员审批" if admin_required else "没有该命名空间的发布审批权限")
    snapshot = json.loads(deployment.get("policy_snapshot") or "{}")
    namespace = store.get_namespace(deployment["namespace_id"])
    if not namespace:
        raise NotFound("发布任务所在的命名空间不存在")
    required = max(1, int(snapshot.get("min_approvals", 1)),
                   required_approvals(effective_policy(namespace["policy"]), namespace["tier"]))
    # 已失效的批准（审批人停用或失去权限）不计入：要求的人数相应增加
    required += store.count_approvals(deployment["id"], "approved") - valid_approval_count(store, deployment)
    result = store.record_approval_transition(deployment["id"], actor.id, decision, comment, required)
    result["approvals"] = store.list_approvals(deployment["id"])
    return result


def decide_request(store: Store, me: Principal, deployment_id: int, decision: str, data: Any) -> Dict[str, Any]:
    """审批接口：无权审批的任务按不存在处理。"""
    if not isinstance(data, dict):
        raise ValueError("审批请求格式错误")
    reject_unknown(data, {"comment"}, "审批请求包含未知字段")
    record = store.get_deployment(deployment_id)
    if not record or not record.get("service_id") or not store.get_service(record["service_id"]):
        raise NotFound("发布任务不存在")
    admin_required = decision == "approved" and requires_admin_approval(store, record)
    if not (me.is_platform_admin if admin_required else me.can_namespace(record["namespace_id"], "deploy.approve")):
        raise NotFound("发布任务不存在")
    return decide(store, record, me, decision, text_field(data, "comment", 500))


# ------------------------------------------------------------------ 发布记录
def enrich(store: Store, me: Principal, record: Dict[str, Any], lookup: Lookup) -> Dict[str, Any]:
    """补充项目、命名空间名称，以及当前账号能否审批。"""
    ns, project = lookup.namespace(record.get("namespace_id")), lookup.project(record.get("project_id"))
    pending = record["status"] == "pending_approval"
    admin_required = requires_admin_approval(store, record, ns) if ns else False
    record.update(project_name=project["name"] if project else "",
                  namespace_name=ns["name"] if ns else "", cluster_name=ns["cluster_name"] if ns else "",
                  can_approve=pending and can_decide(store, me, record, admin_required=admin_required),
                  can_reject=pending and can_decide(store, me, record, "rejected"),
                  require_admin_approval=admin_required)
    return record


def list_records(store: Store, me: Principal, *, limit: int, offset: int, **filters: Any
                 ) -> Tuple[List[Dict[str, Any]], int]:
    """项目上有 deploy.view / app.view，或命名空间上有 namespace.view 的发布记录可见。"""
    deploy_projects, app_projects = me.project_ids("deploy.view"), me.project_ids("app.view")
    namespaces = me.namespace_ids("namespace.view")
    if deploy_projects is None or app_projects is None or namespaces is None:
        projects = namespaces = None  # 全局可见
    else:
        projects = sorted(set(deploy_projects) | set(app_projects))
    target_id, app_id = filters.pop("target_id", None), filters.pop("app_id", None)
    items, total = store.list_deployments(target_id, app_id, limit, offset, projects, namespaces, **filters)
    lookup = Lookup(store)
    return [enrich(store, me, item, lookup) for item in items], total


def visible_record(store: Store, me: Principal, deployment_id: int) -> Dict[str, Any]:
    record = store.get_deployment(deployment_id)
    if record and (me.can_project(record.get("project_id"), "deploy.view")
                   or (record.get("service_id") and (me.can_project(record["project_id"], "app.view")
                                                     or me.can_namespace(record.get("namespace_id"),
                                                                         "namespace.view")))):
        return record
    raise NotFound("发布记录不存在")


def record_detail(store: Store, me: Principal, deployment_id: int) -> Dict[str, Any]:
    record = visible_record(store, me, deployment_id)
    record["approvals"] = store.list_approvals(deployment_id) if record.get("service_id") else []
    return enrich(store, me, record, Lookup(store))
