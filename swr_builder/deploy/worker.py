"""服务发布 worker：从 SQLite 队列领取发布任务，更新 Kubernetes Deployment 镜像并等待滚动发布完成。

并发与故障处理：
- 领取任务时获得租约（lease_owner + lease_until + fencing_token），运行期间每 LEASE_RENEW_SECONDS 续租一次
- 每次读写 Kubernetes 前确认租约仍有效；状态更新都带 fencing token，过期的 worker 无法覆盖结果
- 租约过期仍处于 applying/verifying 的任务被判定为 unknown（无法确认集群状态，需人工核对），释放服务的发布槽
- 主循环捕获异常并退避，单次数据库或代码错误不会让 worker 退出
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import socket
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import httpx

from ..authz import has_namespace_permission
from ..builder import JobLog
from ..policy import deployment_policy_block
from ..registry import parse_image_ref
from ..services.namespaces import effective_policy, required_approvals
from ..services.releases import valid_approval_count
from ..store import utcnow
from .k8s_client import KubeClient, deployment_path

logger = logging.getLogger("swr_builder")

LEASE_SECONDS = 300
LEASE_RENEW_SECONDS = 60
IDLE_POLL_SECONDS = 0.5
ERROR_BACKOFF_SECONDS = 5
APPROVAL_EXPIRY_HOURS = 24
MAX_ROLLOUT_SECONDS = 3600
FINAL_STATUSES = {"success", "failed", "cancelled", "unknown", "expired"}


class ReturnedToApproval(Exception):
    """发布策略变化后审批人数不足，任务已退回审批队列。"""


class ServiceDeploymentWorker:
    def __init__(self, settings, store, box, events, transport=None):
        self.settings, self.store, self.box, self.events, self.transport = settings, store, box, events, transport
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}:{secrets.token_hex(4)}"
        self._task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------ 生命周期
    def start(self) -> None:
        self.store.mark_interrupted_service_deployments_unknown(
            "worker interrupted during Kubernetes write; verify cluster state before resubmitting")
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def _loop(self) -> None:
        last_maintenance = 0.0
        while True:
            try:
                now = asyncio.get_running_loop().time()
                if now - last_maintenance >= 30:
                    self._maintenance()
                    last_maintenance = now
                deployment = self.store.claim_next_service_deployment(self.worker_id, lease_seconds=LEASE_SECONDS)
                if deployment is None:
                    await asyncio.sleep(IDLE_POLL_SECONDS)
                    continue
                await self.run(deployment)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 worker 不能因单次错误退出
                logger.exception("服务发布 worker 出错，%s 秒后重试", ERROR_BACKOFF_SECONDS)
                await asyncio.sleep(ERROR_BACKOFF_SECONDS)

    def _maintenance(self) -> None:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=APPROVAL_EXPIRY_HOURS)).isoformat(timespec="seconds")
        self.store.expire_pending_approvals(cutoff)
        stale = self.store.expire_stale_service_leases(
            "deployment lease expired; worker stopped responding, verify cluster state before resubmitting")
        if stale:
            logger.warning("%s 个发布任务的租约已过期，已标记为结果待确认", stale)

    def log_path(self, deployment_id: int):
        return self.settings.data_path / "deployments" / str(deployment_id) / "deploy.log"

    # ------------------------------------------------------------------ 执行一个发布任务
    async def run(self, deployment: Dict[str, Any]) -> None:
        deployment_id, token = deployment["id"], deployment["fencing_token"]
        service = self.store.get_service(deployment["service_id"])
        job = self.store.get_job(deployment["job_id"])
        revision = self.store.get_revision(deployment["service_id"], deployment["revision_id"])
        namespace = self.store.get_namespace(deployment["namespace_id"])
        cluster = self.store.get_cluster_record(namespace["cluster_id"]) if namespace else None
        credential: Dict[str, Any] = {}
        if cluster:
            try:
                credential = json.loads(self.box.decrypt(cluster["credential_encrypted"]))
            except (ValueError, TypeError):
                credential = {}
        log = JobLog(self.log_path(deployment_id), [credential.get("token", "")])
        heartbeat = asyncio.create_task(self._renew_lease(deployment_id, token))
        progress = {"write_started": False}
        try:
            if not service or not job or not revision or not namespace or not cluster:
                raise ValueError("部署快照引用的服务、构建、修订、命名空间或集群已不存在")
            if revision["spec"].get("mode") != "image_only":
                raise ValueError("managed 清单渲染与 Server-Side Apply 尚未在此 worker 阶段启用")
            if (cluster["auth_type"] == "sa_token" and not credential.get("token")) or \
                    cluster["auth_type"] not in ("sa_token", "client_cert"):
                raise ValueError("集群凭据类型不受当前 worker 支持")
            if cluster["auth_type"] == "client_cert" and not (credential.get("client_cert_pem") and credential.get("client_key_pem")):
                raise ValueError("集群客户端证书凭据不完整")
            self._precheck(deployment, namespace)
            await self._apply(deployment, revision, namespace, cluster, credential, log, progress)
            if self.store.complete_service_deployment(deployment_id, self.worker_id, token, service["id"],
                                                      revision["id"], "Deployment rollout completed"):
                log.info("Deployment 已达到期望镜像和可用副本数")
        except ReturnedToApproval:
            if not self.store.mark_service_deployment_pending_approval(deployment_id, self.worker_id, token):
                self._fail(deployment_id, token, progress, RuntimeError("deployment lease lost while returning to approval"), log)
            else:
                log.info("发布策略已变化，任务退回审批队列")
        except asyncio.CancelledError:
            if self.store.service_deployment_lease_valid(deployment_id, self.worker_id, token):
                written = progress["write_started"]
                self.store.update_service_deployment(
                    deployment_id, self.worker_id, token, status="unknown" if written else "cancelled",
                    error=("worker stopped during external operation; verify cluster state" if written
                           else "worker stopped before cluster write"),
                    finished_at=utcnow(), lease_owner=None, lease_until=None)
            raise
        except Exception as exc:  # noqa: BLE001
            self._fail(deployment_id, token, progress, exc, log)
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            log.close()
            result = self.store.get_deployment(deployment_id)
            if result and result["status"] in FINAL_STATUSES:
                self.store.add_audit("service-worker", "deploy." + result["status"], str(deployment_id))
                self.events.emit("deploy.succeeded" if result["status"] == "success" else "deploy.failed",
                                 {"deployment": result})

    def _fail(self, deployment_id: int, token: int, progress: Dict[str, bool], exc: Exception, log: JobLog) -> None:
        state = "unknown" if progress["write_started"] else "failed"
        if isinstance(exc, ValueError):
            message = str(exc)
        elif state == "unknown":
            message = "外部操作结果无法确认，请核对集群后处理"
        else:
            message = "部署 worker 内部错误"
            logger.exception("发布任务 #%s 内部错误", deployment_id, exc_info=exc)
        log.error(message)
        self.store.update_service_deployment(deployment_id, self.worker_id, token, status=state, error=message[:1000],
                                             finished_at=utcnow(), lease_owner=None, lease_until=None)

    async def _renew_lease(self, deployment_id: int, token: int) -> None:
        while True:
            await asyncio.sleep(LEASE_RENEW_SECONDS)
            try:
                if not self.store.renew_service_deployment_lease(deployment_id, self.worker_id, token, LEASE_SECONDS):
                    logger.warning("发布任务 #%s 续租失败，后续写入会被拒绝", deployment_id)
                    return
            except Exception:  # noqa: BLE001 续租失败时由租约检查兜底
                logger.exception("发布任务 #%s 续租出错", deployment_id)

    def _precheck(self, deployment: Dict[str, Any], namespace: Dict[str, Any]) -> None:
        """发起人仍有权限、策略未阻止、审批人数满足、镜像组织在允许范围内。领取后与写入前各检查一次。"""
        actor = self.store.get_user(int(deployment["created_by"])) if deployment.get("created_by") else None
        if (not actor or not actor["enabled"] or actor["must_change_password"]
                or not has_namespace_permission(self.store, actor, namespace["id"], "service.deploy")):
            raise ValueError("发布发起人已停用或不再拥有该命名空间的发布权限")
        policy = effective_policy(namespace["policy"])
        blocked = deployment_policy_block(policy)
        if blocked:
            raise ValueError(blocked)
        snapshot = json.loads(deployment.get("policy_snapshot") or "{}")
        required = max(required_approvals(policy, namespace["tier"]),
                       required_approvals(snapshot, namespace["tier"]))
        if required and valid_approval_count(self.store, deployment) < required:
            raise ReturnedToApproval()
        image_ref = parse_image_ref(deployment["image"])
        image_org = image_ref.repo.split("/", 1)[0] if image_ref else ""
        if not image_org or image_org not in policy["allowed_registry_orgs"]:
            raise ValueError("部署镜像组织不符合当前命名空间策略")

    def _check_lease(self, deployment_id: int, token: int, stage: str) -> None:
        if not self.store.service_deployment_lease_valid(deployment_id, self.worker_id, token):
            raise RuntimeError(f"deployment lease expired {stage}")

    async def _apply(self, deployment, revision, namespace, cluster, credential, log: JobLog,
                     progress: Dict[str, bool]) -> None:
        deployment_id, token = deployment["id"], deployment["fencing_token"]
        target = revision["spec"]["target"]
        path = deployment_path(namespace["name"], target["name"])
        async with KubeClient(cluster["server"], credential, self.transport, timeout=30) as kube:
            self._check_lease(deployment_id, token, "before Kubernetes access")
            response = await kube.request("GET", path)
            if response.status_code != 200:
                raise ValueError(f"读取 Deployment 失败，Kubernetes API HTTP {response.status_code}")
            current = response.json()
            containers = current.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
            if not any(item.get("name") == target["container"] for item in containers):
                raise ValueError("目标 Deployment 中不存在配置的容器")
            # 读取期间策略可能被修改：写入前用最新的命名空间数据再检查一次
            latest = self.store.get_namespace(namespace["id"])
            if not latest:
                raise ValueError("目标命名空间已不存在")
            self._precheck(deployment, latest)
            self._check_lease(deployment_id, token, "before Kubernetes write")
            body = {"spec": {"template": {
                "metadata": {"annotations": {"swr-builder/deployment-id": str(deployment_id),
                                             "swr-builder/revision": str(revision["id"])}},
                "spec": {"containers": [{"name": target["container"], "image": deployment["image"]}]}}}}
            progress["write_started"] = True
            response = await kube.request("PATCH", path, json=body,
                                          headers={"Content-Type": "application/strategic-merge-patch+json"})
            if response.status_code not in (200, 201):
                if 400 <= response.status_code < 500:
                    progress["write_started"] = False
                raise ValueError(f"更新 Deployment 失败，Kubernetes API HTTP {response.status_code}")
            uid = response.json().get("metadata", {}).get("uid") or current.get("metadata", {}).get("uid")
            if not self.store.update_service_deployment(deployment_id, self.worker_id, token, status="verifying"):
                raise RuntimeError("deployment lease lost after Kubernetes write")
            timeout = min(int(effective_policy(latest["policy"])["rollout_timeout"]), MAX_ROLLOUT_SECONDS)
            await self._wait_rollout(kube, path, deployment, target, uid, timeout)

    async def _wait_rollout(self, kube: KubeClient, path: str, deployment, target, uid, timeout: int) -> None:
        deployment_id, token = deployment["id"], deployment["fencing_token"]
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            self._check_lease(deployment_id, token, "while waiting for rollout")
            try:
                response = await kube.request("GET", path)
            except httpx.HTTPError:
                raise RuntimeError("发布后读取 Deployment 失败") from None
            if response.status_code != 200:
                raise RuntimeError(f"发布后读取 Deployment 失败，HTTP {response.status_code}")
            current = response.json()
            metadata = current.get("metadata", {})
            template = current.get("spec", {}).get("template", {})
            if uid and metadata.get("uid") != uid:
                raise ValueError("Deployment 已被删除并重建，发布结果需人工确认")
            running = template.get("spec", {}).get("containers", [])
            image = next((item.get("image") for item in running if item.get("name") == target["container"]), None)
            annotations = template.get("metadata", {}).get("annotations", {})
            if image != deployment["image"] or annotations.get("swr-builder/deployment-id") != str(deployment_id):
                raise ValueError("Deployment 模板已被其他操作覆盖")
            status = current.get("status", {})
            desired = current.get("spec", {}).get("replicas", 1)
            generation = metadata.get("generation")
            observed = generation is None or status.get("observedGeneration", 0) >= generation
            if observed and all(status.get(field, 0) == desired for field in ("updatedReplicas", "availableReplicas", "replicas")):
                return
            if any(condition.get("reason") == "ProgressDeadlineExceeded" for condition in status.get("conditions", [])):
                raise ValueError("Kubernetes rollout ProgressDeadlineExceeded")
            await asyncio.sleep(2)
        raise ValueError("Kubernetes rollout timed out; inspect workload before retrying")
