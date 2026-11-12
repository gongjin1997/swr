"""服务发布 worker：从数据库队列领取发布任务，写入 Kubernetes 并等待滚动发布完成。

- 镜像按构建的版本号引用（仓库:版本号）；写入集群前核对该版本号仍指向构建时记录的 digest；
- 纳管服务（image_only）：只更新目标容器的镜像与两个 Pod 模板注解（strategic merge patch）；
- 托管服务（managed）：按发布快照重新渲染对象清单（此时才解密敏感值），先对全部对象做服务端 dry-run，
  全部通过后用 Server-Side Apply 逐个下发（见 swr_builder/delivery/apply.py）。

并发与故障处理：
- 领取任务时获得租约（lease_owner + lease_until + fencing_token），运行期间每 LEASE_RENEW_SECONDS 续租一次
- 每次读写 Kubernetes 前确认租约仍有效；状态更新都带 fencing token，过期的 worker 无法覆盖结果
- 租约过期仍处于 applying/verifying 的任务被判定为 unknown（无法确认集群状态，需人工核对），释放服务的发布槽
- 主循环捕获异常并退避，单次数据库或代码错误不会让 worker 退出

部署中心开关（系统设置 › 启用部署中心）关闭时：不领取新任务，排队中的任务取消；已领取的任务在写入集群前
停止并取消，已开始写入的任务照常完成（中途停止会让集群处于不确定状态）。
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

from ..access import load_principal
from ..builder import JobLog
from ..delivery.apply import apply_objects, failures
from ..delivery.render import render
from ..delivery.resolve import resolve
from ..policy import deployment_policy_block
from ..registry import RegistryError, parse_image_ref
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
DISABLED_POLL_SECONDS = 2
FINAL_STATUSES = {"success", "failed", "cancelled", "unknown", "expired"}
DEPLOY_DISABLED = "部署中心已关闭，发布已取消，未写入集群；开启后请重新提交"


class ReturnedToApproval(Exception):
    """发布策略变化后审批人数不足，任务已退回审批队列。"""


class DeployDisabled(Exception):
    """系统设置关闭了“启用部署中心”：写入集群前停止，任务标记为已取消。"""


class ServiceDeploymentWorker:
    def __init__(self, settings, store, box, events, transport=None, registry=None):
        self.settings, self.store, self.box, self.events, self.transport = settings, store, box, events, transport
        self.registry = registry  # RegistryClient：写入集群前核对版本号指向的 digest
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
                if not self.settings.general.deploy_enabled:
                    # 部署中心关闭：不领取新的发布；排队中的发布直接取消，避免重新开启后旧的发布突然执行
                    self._cancel_queued()
                    await asyncio.sleep(DISABLED_POLL_SECONDS)
                    continue
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

    def _cancel_queued(self) -> None:
        cancelled = self.store.cancel_queued_service_deployments(DEPLOY_DISABLED)
        if cancelled:
            logger.warning("部署中心已关闭，取消了 %s 个排队中的发布", len(cancelled))
        for deployment_id in cancelled:
            self.store.add_audit("service-worker", "deploy.cancelled", str(deployment_id), "deploy center disabled")
            record = self.store.get_deployment(deployment_id)
            if record:
                self.events.emit("deploy.failed", {"deployment": record})

    def log_path(self, deployment_id: int):
        return self.settings.data_path / "deployments" / str(deployment_id) / "deploy.log"

    # ------------------------------------------------------------------ 执行一个发布任务
    async def run(self, deployment: Dict[str, Any]) -> None:
        deployment_id, token = deployment["id"], deployment["fencing_token"]
        service = self.store.get_service(deployment["service_id"])
        job = self.store.get_job(deployment["job_id"]) if deployment.get("job_id") else None
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
            if not service or (deployment.get("job_id") and not job) or not revision or not namespace or not cluster:
                raise ValueError("部署快照引用的服务、构建、修订、命名空间或集群已不存在")
            mode = revision["spec"].get("mode")
            if mode not in ("image_only", "managed"):
                raise ValueError("服务修订的模式无效")
            if (cluster["auth_type"] == "sa_token" and not credential.get("token")) or \
                    cluster["auth_type"] not in ("sa_token", "client_cert"):
                raise ValueError("集群凭据类型不受当前 worker 支持")
            if cluster["auth_type"] == "client_cert" and not (credential.get("client_cert_pem") and credential.get("client_key_pem")):
                raise ValueError("集群客户端证书凭据不完整")
            self._precheck(deployment, namespace)
            await self._verify_image(deployment, log)
            if mode == "managed":
                await self._apply_managed(deployment, service, revision, namespace, cluster, credential, log, progress)
            else:
                await self._apply(deployment, revision, namespace, cluster, credential, log, progress)
            if self.store.complete_service_deployment(deployment_id, self.worker_id, token, service["id"],
                                                      revision["id"], "Deployment rollout completed"):
                log.info("Deployment 已达到期望镜像和可用副本数")
        except ReturnedToApproval:
            if not self.store.mark_service_deployment_pending_approval(deployment_id, self.worker_id, token):
                self._fail(deployment_id, token, progress, RuntimeError("deployment lease lost while returning to approval"), log)
            else:
                log.info("发布策略已变化，任务退回审批队列")
        except DeployDisabled as exc:
            if progress["write_started"]:  # 不会发生：只在写入集群前检查
                self._fail(deployment_id, token, progress, RuntimeError(str(exc)), log)
            else:
                log.info(str(exc))
                self.store.update_service_deployment(deployment_id, self.worker_id, token, status="cancelled",
                                                     error=str(exc), finished_at=utcnow(), lease_owner=None,
                                                     lease_until=None)
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
        """部署中心开启、发起人仍有权限、策略未阻止、审批人数满足、镜像组织在允许范围内。领取后与写入前各检查一次。"""
        if not self.settings.general.deploy_enabled:
            raise DeployDisabled(DEPLOY_DISABLED)
        actor = self.store.get_user(int(deployment["created_by"])) if deployment.get("created_by") else None
        if (not actor or not actor["enabled"] or actor["must_change_password"]
                or not load_principal(self.store, actor).can_namespace(namespace["id"], "service.deploy")):
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

    async def _verify_image(self, deployment: Dict[str, Any], log: JobLog) -> None:
        """发布按版本号（仓库:版本号）下发：写入集群前确认该版本号仍指向构建时记录的 digest。
        平台不覆盖已有版本，这里防止版本号在平台之外被覆盖后，发布了与构建、审批时不同的镜像。

        只核对平台构建的镜像（有构建记录、按版本号引用、在当前配置的镜像仓库中）；接管发布沿用线上镜像，不需要核对。"""
        image = deployment.get("image") or ""
        ref = parse_image_ref(image)
        if (self.registry is None or not deployment.get("job_id") or not ref or "@" in image
                or ref.host.lower() != self.settings.registry.host.lower()):
            return
        recorded = deployment.get("digest") or ""
        try:
            current = await self.registry.manifest_digest(ref.repo, ref.reference)
        except RegistryError as exc:
            raise ValueError(f"无法从镜像仓库核对版本 {ref.repo}:{ref.reference}：{exc}") from None
        if current != recorded:
            raise ValueError(f"镜像 {ref.repo}:{ref.reference} 当前指向 {current[:19]}…，与构建时的 {recorded[:19]}… 不一致"
                             "（版本号可能在平台之外被覆盖），未写入集群")
        log.info(f"已核对镜像版本：{ref.repo}:{ref.reference} 仍是构建时的 {recorded[:19]}…")

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

    async def _apply_managed(self, deployment, service, revision, namespace, cluster, credential, log: JobLog,
                             progress: Dict[str, bool]) -> None:
        """托管服务：按快照渲染 → 全部对象 dry-run → 复核权限与策略 → Server-Side Apply → 等待滚动完成。"""
        deployment_id, token = deployment["id"], deployment["fencing_token"]
        manifest = self.store.get_deployment_manifest(deployment_id)
        if not manifest:
            raise ValueError("发布单缺少渲染快照，请重新提交发布")
        inputs, _ = resolve(self.store, self.box, service, revision["spec"], deployment["image"],
                            pinned=manifest["inputs"], reveal=True, revision_id=revision["id"],
                            deployment_id=deployment_id)
        objects = render(inputs)
        for warning in inputs.warnings:
            log.info("配置提示：" + warning)
        previous = self.store.last_success_manifest(service["id"])
        previous_keys = [(o["kind"], o["metadata"]["name"]) for o in (previous or {}).get("objects", [])]
        workload = next(o for o in objects if o["kind"] == "Deployment")
        target = {"name": workload["metadata"]["name"],
                  "container": workload["spec"]["template"]["spec"]["containers"][0]["name"]}
        path = deployment_path(namespace["name"], target["name"])
        async with KubeClient(cluster["server"], credential, self.transport, timeout=30) as kube:
            self._check_lease(deployment_id, token, "before Kubernetes access")
            # 只有接管发布可以接管集群中已有、不由平台管理的同名对象
            takeover = deployment.get("kind") == "adopt"
            log.info(f"服务端校验 {len(objects)} 个对象（dryRun=All）")
            checks = await apply_objects(kube, namespace["name"], objects, dry_run=True, previous=previous_keys,
                                         service_id=service["id"], log=log, takeover=takeover)
            problems = failures(checks)
            if problems:
                raise ValueError("服务端校验未通过，未写入集群：" + "；".join(problems)[:900])
            latest = self.store.get_namespace(namespace["id"])
            if not latest:
                raise ValueError("目标命名空间已不存在")
            self._precheck(deployment, latest)
            self._check_lease(deployment_id, token, "before Kubernetes write")
            progress["write_started"] = True
            results = await apply_objects(kube, namespace["name"], objects, dry_run=False, previous=previous_keys,
                                          service_id=service["id"], log=log, takeover=takeover)
            problems = failures(results)
            if problems:
                raise ValueError("部分对象下发失败，请核对集群：" + "；".join(problems)[:900])
            uid = next((r.get("uid") for r in results if r["kind"] == "Deployment"), "")
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
