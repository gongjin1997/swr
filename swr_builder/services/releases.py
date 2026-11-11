"""服务发布：排队（幂等、策略、审批要求）与审批决定。实际执行见 deploy/worker.py。"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from typing import Any, Dict, Optional

from ..access import Principal, load_principal, role_index, same_user
from ..errors import Conflict, Forbidden, NotFound, NotImplementedYet, Unprocessable
from ..policy import deployment_policy_block
from ..registry import parse_image_ref
from ..store import Store
from .namespaces import effective_policy, required_approvals

DEPLOYMENT_RESPONSE_FIELDS = ("id", "service_id", "job_id", "revision_id", "kind", "status", "digest", "created_at")
IDEMPOTENCY_KEY_RE = re.compile(r"[A-Za-z0-9._:-]{8,128}")
DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")


def deployment_response(deployment: Dict[str, Any]) -> Dict[str, Any]:
    return {key: deployment.get(key) for key in DEPLOYMENT_RESPONSE_FIELDS}


def request_hash(kind: str, payload: Dict[str, Any]) -> str:
    raw = json.dumps({"kind": kind, "payload": payload}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def requires_admin_approval(store: Store, deployment: Dict[str, Any],
                            namespace: Optional[Dict[str, Any]] = None) -> bool:
    """生产环境、命名空间策略或提交时的策略快照要求平台管理员确认。"""
    snapshot = json.loads(deployment.get("policy_snapshot") or "{}")
    if namespace is None and deployment.get("namespace_id"):
        namespace = store.get_namespace(deployment["namespace_id"])
    return bool(snapshot.get("require_admin_approval")) or bool(namespace and (
        namespace["tier"] == "prod" or namespace["policy"].get("require_admin_approval")))


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


def check_idempotency_key(key: str) -> None:
    if not IDEMPOTENCY_KEY_RE.fullmatch(key):
        raise ValueError("Idempotency-Key 格式无效")


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
    namespace = store.get_namespace(service["namespace_id"])
    policy = effective_policy(namespace["policy"])
    status = "pending_approval" if required_approvals(policy, namespace["tier"]) else "queued"
    image_ref = parse_image_ref(job["image"])
    if not image_ref:
        raise Unprocessable("成功构建记录的镜像引用无效")
    image = image_ref.host + "/" + image_ref.repo + "@" + digest
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
    try:
        deployment = store.create_idempotent_service_deployment(
            target_id=None, target_name=service["name"], target_type="kubernetes", environment=namespace["tier"],
            app_id=service["app_id"], app_name=job["app_name"], job_id=job["id"], image=image,
            project_id=service["project_id"], service_id=service["id"], namespace_id=service["namespace_id"],
            revision_id=revision["id"], kind=kind, digest=digest, idempotency_key=idempotency_key,
            request_hash=payload_hash, policy_snapshot=json.dumps(approval_policy, ensure_ascii=False),
            status=status, trigger="manual", operator=actor.get("display_name") or actor["username"],
            created_by=actor["id"], remark=remark[:200])
    except sqlite3.IntegrityError as exc:
        if "UNIQUE constraint failed" in str(exc):
            raise Conflict("部署请求冲突，请使用新的幂等键") from None
        raise
    store.add_audit(actor["username"], "deploy.submit", str(deployment["id"]),
                    f'service={service["id"]};kind={kind};status={status};revision={revision["id"]}')
    return deployment


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
    required = max(1, int(snapshot.get("min_approvals", 1)),
                   required_approvals(effective_policy(namespace["policy"]), namespace["tier"]))
    required += store.count_approvals(deployment["id"], "approved") - valid_approval_count(store, deployment)
    try:
        result = store.record_approval_transition(deployment["id"], actor.id, decision, comment, required)
    except LookupError:
        raise NotFound("发布任务不存在") from None
    except PermissionError:
        raise Forbidden("发布发起人不能审批自己的任务") from None
    except FileExistsError:
        raise Conflict("当前用户已审批该任务") from None
    except RuntimeError:
        raise Conflict("发布任务不在待审批状态") from None
    result["approvals"] = store.list_approvals(deployment["id"])
    return result

