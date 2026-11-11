"""工作台概览：当前账号可见范围内的汇总数据与待办（待我审批的发布）。"""
from __future__ import annotations

from typing import Any, Dict

from ..access import Principal
from ..store import Store
from . import builds, catalog, projects, releases

RECENT_JOBS = 6
PENDING_SCAN = 100


def overview(store: Store, me: Principal) -> Dict[str, Any]:
    result: Dict[str, Any] = {"projects": len(projects.list_projects(store, me)) if me.can_anywhere("project.view") else 0}
    if me.can_anywhere("app.view"):
        result["apps"] = len(store.list_apps(me.project_ids("app.view")))
    if me.can_anywhere("job.view"):
        recent, total = builds.list_jobs(store, me, limit=RECENT_JOBS)
        counts = {status: builds.list_jobs(store, me, status=status, limit=1)[1]
                  for status in ("queued", "running", "success", "failed")}
        result["jobs"] = {"total": total, "active": counts["queued"] + counts["running"],
                          "success": counts["success"], "failed": counts["failed"], "recent": recent}
    if me.can_anywhere("app.view") or me.can_anywhere("namespace.view"):
        result["services"] = len(catalog.list_services(store, me))
        pending, _ = releases.list_records(store, me, limit=PENDING_SCAN, offset=0, status="pending_approval")
        result["approvals"] = [{key: item[key] for key in ("id", "target_name", "project_name", "namespace_name",
                                                            "cluster_name", "environment", "operator", "created_at",
                                                            "require_admin_approval", "service_id")}
                               for item in pending if item["can_approve"]]
    return result
