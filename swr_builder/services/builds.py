"""构建任务：提交（校验 → 建任务记录 → 保存/解压上传文件 → 入队）、构建记录、取消。"""
from __future__ import annotations

import asyncio
import functools
import shutil
from typing import Any, Dict, List, Optional, Tuple

from ..access import Principal, same_user
from ..apps import MODES
from ..builder import FINAL_STATUSES, BuildManager
from ..config import Settings
from ..dockerfile import extract_zip_safe, plan_files
from ..errors import DomainError, Forbidden
from ..fileio import read_log, save_upload
from ..planner import PlanError, image_repo_of
from ..store import Store, utcnow
from ..versioning import is_valid_tag
from .access import app_or_404, job_or_404
from .apps import validate_image_policy

TRUE_VALUES = ("1", "true", "yes", "on")
JOB_STATUSES = ("queued", "running", "success", "failed", "cancelled")


def list_jobs(store: Store, me: Principal, *, app_id: Optional[int] = None, project_id: Optional[int] = None,
              status: Optional[str] = None, limit: int = 30, offset: int = 0) -> Tuple[List[Dict[str, Any]], int]:
    """当前账号可见的构建记录；可按应用、项目、状态筛选。"""
    if status is not None and status not in JOB_STATUSES:
        raise ValueError("构建状态无效")
    projects = me.project_ids("job.view")
    if app_id is not None:
        app = app_or_404(store, me, app_id)
        project_id = app["project_id"] if project_id in (None, app["project_id"]) else -1
    if project_id is not None:
        projects = [project_id] if me.can_project(project_id, "job.view") else []
    return store.list_jobs(app_id, status, min(max(limit, 1), 100), max(offset, 0), projects)


def job_log(store: Store, manager: BuildManager, me: Principal, job_id: int, offset: int) -> Dict[str, Any]:
    job = job_or_404(store, me, job_id)
    text, new_offset, size = read_log(manager.log_path(job_id), offset)
    return {"text": text, "offset": new_offset, "status": job["status"], "step": job["step"],
            "finished": job["status"] in FINAL_STATUSES and new_offset >= size}


async def cancel_job(store: Store, manager: BuildManager, me: Principal, job_id: int) -> None:
    """项目上有 job.cancel 可取消任何任务；只有 job.cancel.own 时只能取消自己提交的任务。"""
    job = job_or_404(store, me, job_id)
    any_job = me.can_project(job["project_id"], "job.cancel")
    own_job = me.can_project(job["project_id"], "job.cancel.own") and same_user(job.get("created_by"), me.id)
    if not any_job and not own_job:
        raise Forbidden("只能取消自己提交的构建")
    if not await manager.cancel(job_id):
        raise DomainError("任务已经结束，无法取消")


async def submit_build(store: Store, settings: Settings, manager: BuildManager, user: Dict[str, Any],
                       app: Dict[str, Any], *, mode: str, tag: str, base_tag: str, remark: str,
                       extract_zip: str, uploads: List[Any]) -> int:
    validate_image_policy(store, settings, app)
    mode = mode or app["default_mode"]
    tag, base_tag = tag.strip(), base_tag.strip()
    if mode not in MODES:
        raise DomainError("构建方式只能是 incremental 或 full")
    if tag and not is_valid_tag(tag):
        raise DomainError("版本号只能包含字母、数字、. _ -，不能以 . 或 - 开头")
    if base_tag and not is_valid_tag(base_tag):
        raise DomainError("基础版本不合法")
    extract = extract_zip.strip().lower() in TRUE_VALUES
    try:
        entries = plan_files(app["target_dir"], app["target_name"], [f.filename or "" for f in uploads], extract)
        image_repo = image_repo_of(settings, app)
    except (ValueError, PlanError) as exc:
        raise DomainError(str(exc)) from None

    job_id = store.create_job(
        app_id=app["id"], app_name=app["name"], repository=image_repo, requested_mode=mode, mode=mode,
        requested_tag=tag or None, requested_base_tag=base_tag or None, status="queued", step="queued",
        operator=(user.get("display_name") or user["username"])[:80], created_by=user["id"],
        remark=remark.strip()[:200], project_id=app["project_id"], app_snapshot=app, created_at=utcnow())
    try:
        saved = await _save_files(manager, settings, job_id, uploads, entries)
    except ValueError as exc:
        shutil.rmtree(manager.job_dir(job_id), ignore_errors=True)
        store.delete_job(job_id)
        raise DomainError(str(exc)) from None
    finally:
        for upload in uploads:
            await upload.close()
    store.update_job(job_id, files=saved)
    manager.submit(job_id)
    return job_id


async def _save_files(manager: BuildManager, settings: Settings, job_id: int, uploads: List[Any],
                      entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    context = manager.context_dir(job_id)
    files_dir = context / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    limit = settings.build.max_upload_mb * 1024 * 1024
    loop = asyncio.get_running_loop()
    saved: List[Dict[str, Any]] = []
    for index, (upload, entry) in enumerate(zip(uploads, entries)):
        if entry["extract"]:
            archive = context / f"upload-{index}.zip"
            size, sha = await save_upload(upload, archive, limit)
            # 解压总量上限为单文件上限的 4 倍，防 zip 炸弹
            names = await loop.run_in_executor(None, functools.partial(extract_zip_safe, archive, files_dir, limit * 4))
            archive.unlink()
            saved.append({**entry, "size": size, "sha256": sha, "extracted": len(names), "entries": names[:100]})
        else:
            size, sha = await save_upload(upload, files_dir / entry["path"], limit)
            saved.append({**entry, "size": size, "sha256": sha})
    return saved
