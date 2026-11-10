"""构建任务执行：解析版本 → docker login → docker build → 架构校验 → docker push。"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shlex
import shutil
import traceback
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .config import Settings
from .dockerfile import render_dockerfile
from .planner import BuildPlan, PlanError, repo_path_of, resolve_plan
from .registry import RegistryClient, RegistryError, normalize_arch
from .store import Store, utcnow

logger = logging.getLogger("swr_builder")

FINAL_STATUSES = {"success", "failed", "cancelled"}
_DIGEST_RE = re.compile(r"digest:\s*(sha256:[0-9a-f]{64})")


class BuildError(Exception):
    """构建失败（消息会直接展示给用户）。"""


class JobCancelled(Exception):
    pass


class JobLog:
    """构建日志：逐行写文件，页面按偏移量增量读取。"""

    def __init__(self, path: Path, secrets: List[str]):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._secrets = [s for s in secrets if s and len(s) >= 4]
        self._fh = open(path, "a", encoding="utf-8")

    def raw(self, text: str) -> None:
        for secret in self._secrets:
            text = text.replace(secret, "******")
        self._fh.write(text + "\n")
        self._fh.flush()

    def info(self, message: str) -> None:
        self.raw(f"[{datetime.now():%H:%M:%S}] {message}")

    def warn(self, message: str) -> None:
        self.info(f"[WARN] {message}")

    def error(self, message: str) -> None:
        self.info(f"[ERROR] {message}")

    def step(self, index: int, total: int, title: str) -> None:
        self.raw("")
        self.raw(f"==> [{index}/{total}] {title}    {datetime.now():%Y-%m-%d %H:%M:%S}")

    def close(self) -> None:
        self._fh.close()


def fmt_size(size: Optional[int]) -> str:
    if not size:
        return "-"
    value = float(size)
    for unit in ("B", "KB", "MB"):
        if value < 1024:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.2f} GB"


class BuildManager:
    def __init__(self, settings: Settings, store: Store, registry: RegistryClient, events=None):
        self.events = events
        self.settings = settings
        self.store = store
        self.registry = registry
        self.data_dir = settings.data_path
        self.jobs_dir = self.data_dir / "jobs"
        self.docker_config = self.data_dir / "docker-config"
        self._sem: Optional[asyncio.Semaphore] = None
        # 仓库路径 → (锁, 使用该锁的任务数)；计数归零时删除，避免字典无限增长
        self._repo_locks: Dict[str, Tuple[asyncio.Lock, int]] = {}
        self._procs: Dict[int, Any] = {}
        self._tasks: Dict[int, Any] = {}
        self._cancelled: Set[int] = set()

    # ------------------------------------------------------------------ 生命周期
    async def start(self) -> None:
        self._sem = asyncio.Semaphore(max(1, int(self.settings.build.max_concurrent)))
        self.jobs_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.docker_config.mkdir(mode=0o700, parents=True, exist_ok=True)
        count = self.store.fail_unfinished_jobs("平台重启，任务被中断，请重新提交")
        if count:
            logger.warning("有 %s 个未完成的任务因平台重启被标记为失败", count)

    def resize(self, n: int) -> None:
        if self.has_running_jobs():
            raise ValueError("有构建任务时不能调整并发数")
        self._sem = asyncio.Semaphore(max(1, n))

    async def stop(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    # ------------------------------------------------------------------ 路径/环境
    def job_dir(self, job_id: int) -> Path:
        return self.jobs_dir / str(job_id)

    def context_dir(self, job_id: int) -> Path:
        return self.job_dir(job_id) / "context"

    def log_path(self, job_id: int) -> Path:
        return self.job_dir(job_id) / "build.log"

    @property
    def docker_env(self) -> Dict[str, str]:
        env = dict(os.environ)
        user, password = self.settings.registry.credentials()
        if user and password:
            # 平台使用独立的 docker 配置目录，不影响构建机上其他用户的登录状态
            env["DOCKER_CONFIG"] = str(self.docker_config)
        if self.settings.registry.insecure or self.settings.registry.ca_file:
            # BuildKit normally obtains OAuth tokens in the client process, which
            # does not inherit daemon registry TLS settings. Delegate token auth
            # to the configured builder so its registry trust policy also applies.
            env["BUILDKIT_NO_CLIENT_TOKEN"] = "1"
        return env

    def _secrets(self) -> List[str]:
        _, password = self.settings.registry.credentials()
        return [password, self.settings.registry.sk]

    # ------------------------------------------------------------------ 任务调度
    def submit(self, job_id: int) -> None:
        task = asyncio.ensure_future(self._run(job_id))
        self._tasks[job_id] = task
        task.add_done_callback(lambda _t, j=job_id: self._tasks.pop(j, None))

    async def cancel(self, job_id: int) -> bool:
        job = self.store.get_job(job_id)
        if not job or job["status"] in FINAL_STATUSES:
            return False
        task = self._tasks.get(job_id)
        if task is None:
            self.store.update_job(job_id, status="cancelled", finished_at=utcnow())
            return True
        self._cancelled.add(job_id)
        proc = self._procs.get(job_id)
        if proc is not None and proc.returncode is None:
            try:
                proc.terminate()
            except ProcessLookupError:
                pass
        if job["status"] == "queued":
            task.cancel()
        return True

    def has_running_jobs(self) -> bool:
        """是否有已提交但未结束的构建任务（排队中或构建中）。"""
        return bool(self._tasks)

    def _acquire_repo_lock(self, key: str) -> asyncio.Lock:
        lock, users = self._repo_locks.get(key, (None, 0))
        if lock is None:
            lock = asyncio.Lock()
        self._repo_locks[key] = (lock, users + 1)
        return lock

    def _release_repo_lock(self, key: str) -> None:
        lock, users = self._repo_locks[key]
        if users <= 1:
            del self._repo_locks[key]
        else:
            self._repo_locks[key] = (lock, users - 1)

    def _check_cancel(self, job_id: int) -> None:
        if job_id in self._cancelled:
            raise JobCancelled()

    async def _run(self, job_id: int) -> None:
        job = self.store.get_job(job_id)
        if not job:
            return
        log = JobLog(self.log_path(job_id), self._secrets())
        try:
            assert self._sem is not None, "BuildManager.start() 未调用"
            log.info(f"任务 #{job_id} 已提交，操作人：{job.get('operator') or '-'}")
            app = job["app_snapshot"]
            repo_key = repo_path_of(self.settings, app)
            # 先排同仓库的队，再占全局并发名额：同一仓库排队的任务不会占着名额阻塞其他仓库
            lock = self._acquire_repo_lock(repo_key)
            try:
                if lock.locked():
                    log.info(f"{repo_key} 有其他构建正在进行，等待其完成后再计算版本…")
                async with lock:
                    if self._sem.locked():
                        log.info("构建资源已满，排队等待…")
                    async with self._sem:
                        self._check_cancel(job_id)
                        await self._execute(job_id, job, app, log)
            finally:
                self._release_repo_lock(repo_key)
        except (JobCancelled, asyncio.CancelledError):
            log.warn("任务已取消")
            self.store.update_job(job_id, status="cancelled", finished_at=utcnow())
        except (BuildError, PlanError, RegistryError) as exc:
            log.error(str(exc))
            self.store.update_job(job_id, status="failed", error=str(exc), finished_at=utcnow())
        except Exception as exc:  # noqa: BLE001 兜底，避免任务卡在“构建中”
            log.error(f"平台内部错误：{exc}")
            log.raw(traceback.format_exc())
            self.store.update_job(job_id, status="failed", error=f"平台内部错误：{exc}", finished_at=utcnow())
        finally:
            result = self.store.get_job(job_id)
            if self.events and result and result["status"] in ("success", "failed"):
                self.events.emit("build.succeeded" if result["status"] == "success" else "build.failed", {"job": result})
            self._cancelled.discard(job_id)
            log.close()
            if not self.settings.build.keep_context:
                shutil.rmtree(self.context_dir(job_id), ignore_errors=True)

    # ------------------------------------------------------------------ 构建流程
    async def _execute(self, job_id: int, job: Dict[str, Any], app: Dict[str, Any], log: JobLog) -> None:
        docker = self.settings.build.docker_bin
        total = 4
        self.store.update_job(job_id, status="running", step="resolve", started_at=utcnow())

        log.step(1, total, "解析版本")
        plan = await resolve_plan(self.registry, self.settings, app, job["requested_mode"],
                                  job.get("requested_base_tag"), job.get("requested_tag"))
        log.info(f"镜像仓库：{plan.image_repo}（共 {len(plan.tags)} 个标签）")
        log.info(f"上一版本：{plan.latest or '无'}")
        if plan.mode == "incremental" and plan.base_info:
            info = plan.base_info
            log.info(f"基础版本 {plan.base_tag}：{info.get('layers')} 层，{fmt_size(info.get('size'))}，"
                     f"架构 {info.get('architecture') or '未知'}")
        for note in plan.notes:
            log.warn(note)
        log.info(f"构建方式：{'累加构建' if plan.mode == 'incremental' else '全量构建'}")
        log.info(f"基础镜像：{plan.base_image or '（模板中指定）'}")
        log.info(f"新版本：{plan.new_tag}")
        for item in job.get("files") or []:
            log.info(f"上传文件：{item['name']}（{fmt_size(item.get('size'))}）→ {item['dest']}")

        dockerfile = render_dockerfile(app, plan.mode, base_image=plan.base_image, new_tag=plan.new_tag,
                                       prev_tag=plan.latest or "", image=plan.image)
        context = self.context_dir(job_id)
        (context / "Dockerfile").write_text(dockerfile, encoding="utf-8")
        self.store.update_job(job_id, mode=plan.mode, prev_tag=plan.latest, base_tag=plan.base_tag,
                              base_image=plan.base_image, new_tag=plan.new_tag, image=plan.image,
                              dockerfile=dockerfile, notes=plan.notes)
        log.info("Dockerfile：")
        for line in dockerfile.rstrip("\n").split("\n"):
            log.raw(f"    {line}")
        self._check_cancel(job_id)

        self.store.update_job(job_id, step="login")
        log.step(2, total, "登录镜像仓库")
        await self._docker_login(job_id, log)

        self.store.update_job(job_id, step="build")
        log.step(3, total, "构建镜像")
        if self.settings.registry.insecure or self.settings.registry.ca_file:
            log.info("仓库认证令牌由构建引擎申请，沿用构建引擎的仓库证书配置")
        args = [docker, "build", "-f", str(context / "Dockerfile"), "-t", plan.image]
        if app.get("platform"):
            args += ["--platform", app["platform"]]
        labels = {
            "org.opencontainers.image.version": plan.new_tag,
            "org.opencontainers.image.created": utcnow(),
            "swr-builder.job": str(job_id),
            "swr-builder.mode": plan.mode,
            "swr-builder.base-image": plan.base_image,
            "swr-builder.operator": job.get("operator") or "",
        }
        for key, value in labels.items():
            args += ["--label", f"{key}={value}"]
        args += list(self.settings.build.extra_build_args)
        args.append(str(context))
        code, output = await self._run_cmd(job_id, args, log, self.settings.build.build_timeout)
        if code != 0:
            if "x509:" in output.lower() or "tls: failed to verify certificate" in output.lower():
                raise BuildError("基础镜像仓库或认证服务的 TLS 证书校验失败。已将令牌请求交由构建引擎；"
                                 "请检查当前 builder 的仓库配置：Docker driver 使用 daemon.json 的 insecure-registries/CA，"
                                 "独立 BuildKit 使用 buildkitd.toml 的 registry insecure/ca。"
                                 "证书缺少匹配域名的 SAN 时，仅上传 CA 不能修复域名校验。")
            raise BuildError(f"docker build 失败（退出码 {code}），请查看日志")
        await self._verify_arch(job_id, plan, log)

        self.store.update_job(job_id, step="push")
        log.step(4, total, "推送镜像")
        code, output = await self._run_cmd(job_id, [docker, "push", plan.image], log, self.settings.build.push_timeout)
        if code != 0:
            raise BuildError(f"docker push 失败（退出码 {code}），请查看日志")
        digests = _DIGEST_RE.findall(output)
        digest = digests[-1] if digests else None
        self.store.update_job(job_id, digest=digest)
        if app.get("push_latest"):
            latest_ref = f"{plan.image_repo}:latest"
            code, _ = await self._run_cmd(job_id, [docker, "tag", plan.image, latest_ref], log, 120)
            if code == 0:
                code, _ = await self._run_cmd(job_id, [docker, "push", latest_ref], log,
                                              self.settings.build.push_timeout)
            if code != 0:
                raise BuildError(f"{plan.new_tag} 已推送成功，但推送 latest 标签失败")
        await self._cleanup_local(job_id, plan, app, log)

        self.store.update_job(job_id, status="success", step="done", finished_at=utcnow())
        log.raw("")
        log.info(f"构建成功：{plan.image}")
        if digest:
            log.info(f"镜像摘要：{digest}")

    async def _docker_login(self, job_id: int, log: JobLog) -> None:
        user, password = self.settings.registry.credentials()
        if not user or not password:
            log.warn("没有配置 SWR 用户名/密钥，跳过 docker login（使用构建机上已有的登录状态）")
            return
        args = [self.settings.build.docker_bin, "login", self.settings.registry.host, "-u", user, "--password-stdin"]
        code, output = await self._run_cmd(job_id, args, log, self.settings.build.login_timeout, stdin=password + "\n")
        if code != 0:
            if "x509:" in output.lower() or "certificate is not valid for any names" in output.lower():
                if self.settings.registry.insecure:
                    raise BuildError(
                        f"宿主机 Docker daemon 不信任仓库 {self.settings.registry.host} 的证书。"
                        "系统设置的跳过 TLS 仅作用于平台 Registry API；请在 Docker daemon 的 daemon.json "
                        "将该仓库加入 insecure-registries 并重启 Docker，或修复仓库证书 SAN/信任链。")
                raise BuildError(
                    f"宿主机 Docker daemon 无法验证仓库 {self.settings.registry.host} 的 TLS 证书；"
                    "请修复证书 SAN/信任链，或在仅限测试环境时配置 daemon insecure-registries。")
            raise BuildError("docker login 失败：请检查 SWR 用户名和密钥（需要长期有效登录密钥），"
                             "以及构建机能否访问仓库地址")

    async def _verify_arch(self, job_id: int, plan: BuildPlan, log: JobLog) -> None:
        args = [self.settings.build.docker_bin, "image", "inspect", "--format", "{{.Os}}/{{.Architecture}}", plan.image]
        code, output = await self._run_cmd(job_id, args, log, 60)
        lines = [l for l in output.strip().splitlines() if l.strip()]
        if code != 0 or not lines:
            log.warn("无法读取构建结果的架构信息，跳过架构校验")
            return
        built = normalize_arch(lines[-1].split("/")[-1])
        if plan.expected_arch and built and built != plan.expected_arch:
            raise BuildError(f"架构不一致：构建结果是 {built}，期望 {plan.expected_arch}，已停止推送。"
                             f"如果构建机是 x86，请在应用中把“构建平台”设为 linux/{plan.expected_arch}")
        log.info(f"架构校验通过：{lines[-1]}")

    async def _cleanup_local(self, job_id: int, plan: BuildPlan, app: Dict[str, Any], log: JobLog) -> None:
        if not self.settings.build.cleanup_local_images:
            return
        refs = []
        if plan.mode == "incremental" and plan.base_tag:
            refs.append(plan.base_image)  # 新镜像包含上一版本所有层，删除旧标签不会删掉共享层
        if app.get("push_latest"):
            refs.append(f"{plan.image_repo}:latest")
        for ref in refs:
            await self._run_cmd(job_id, [self.settings.build.docker_bin, "rmi", ref], log, 120)

    async def _run_cmd(self, job_id: int, args: List[str], log: JobLog, timeout: float,
                       stdin: Optional[str] = None) -> Tuple[int, str]:
        log.raw("$ " + " ".join(shlex.quote(a) for a in args))
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                env=self.docker_env, limit=1024 * 1024)
        except FileNotFoundError:
            raise BuildError(f"找不到 docker 命令（{args[0]}）：请确认构建机已安装 Docker，"
                             "或在 config.yaml 的 build.docker_bin 中配置完整路径")
        except PermissionError:
            raise BuildError(f"没有权限执行 {args[0]}")
        self._procs[job_id] = proc
        tail: "deque[str]" = deque(maxlen=200)

        async def pump() -> None:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                text = line.decode("utf-8", "replace").rstrip("\r\n").split("\r")[-1]
                log.raw(text)
                tail.append(text)

        try:
            if stdin is not None and proc.stdin is not None:
                try:
                    proc.stdin.write(stdin.encode())
                    await proc.stdin.drain()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                proc.stdin.close()
            await asyncio.wait_for(asyncio.gather(pump(), proc.wait()), timeout=timeout)
        except asyncio.TimeoutError:
            self._kill(proc)
            await proc.wait()
            raise BuildError(f"命令执行超时（超过 {int(timeout)} 秒）：{' '.join(args[:2])}")
        except asyncio.CancelledError:
            self._kill(proc)
            raise
        finally:
            self._procs.pop(job_id, None)
        self._check_cancel(job_id)
        return (proc.returncode if proc.returncode is not None else -1), "\n".join(tail)

    @staticmethod
    def _kill(proc: Any) -> None:
        if proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
