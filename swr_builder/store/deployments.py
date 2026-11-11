"""旧部署目标、发布记录、审批与发布队列租约。

发布记录状态：pending_approval → queued → applying → verifying → success；
失败时为 failed / rejected / expired / cancelled，外部写入结果无法确认时为 unknown。

并发规则：
- 领取任务用条件更新（WHERE status = 'queued'），同一行只会被一个 worker 领到；
  唯一索引 uq_deployments_active_service 保证同一服务同一时刻只有一个执行中的发布；
- 审批先锁住发布单行，计数与推进状态在同一事务内完成；
- 幂等创建以唯一约束为准，并发重复请求返回已有记录。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from ..errors import Conflict, Forbidden, NotFound
from .db import is_unique_violation
from .schema import DEPLOYMENT_FIELDS, utcnow

ACTIVE = "('applying','verifying')"
APPROVAL_CONSTRAINT = "uq_approvals_approver"
IDEMPOTENCY_CONSTRAINT = "uq_deployments_idempotency"


def _later(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat(timespec="seconds")


class DeploymentsMixin:
    # ------------------------------------------------------------------ 旧部署目标
    @staticmethod
    def _target(row: Dict[str, Any]) -> Dict[str, Any]:
        data = json.loads(row["data"])
        data.update(id=row["id"], name=row["name"], type=row["type"],
                    created_at=row["created_at"], updated_at=row["updated_at"])
        return data

    def list_deploy_targets(self) -> List[Dict[str, Any]]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM deploy_targets ORDER BY lower(name), id").fetchall()
        return [self._target(r) for r in rows]

    def get_deploy_target(self, target_id: int) -> Optional[Dict[str, Any]]:
        with self._db() as db:
            row = db.execute("SELECT * FROM deploy_targets WHERE id = %s", (target_id,)).fetchone()
        return self._target(row) if row else None

    def find_deploy_target_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        with self._db() as db:
            row = db.execute("SELECT * FROM deploy_targets WHERE name = %s", (name,)).fetchone()
        return self._target(row) if row else None

    def save_deploy_target(self, data: Dict[str, Any], target_id: Optional[int] = None) -> Dict[str, Any]:
        body = json.dumps({k: v for k, v in data.items()
                           if k not in ("id", "name", "type", "created_at", "updated_at")}, ensure_ascii=False)
        now = utcnow()
        with self._db() as db, self._unique("部署目标名称已存在"):
            if target_id is None:
                target_id = int(db.execute("INSERT INTO deploy_targets(name, type, data, created_at, updated_at) "
                                           "VALUES (%s, %s, %s, %s, %s) RETURNING id",
                                           (data["name"], data["type"], body, now, now)).fetchone()["id"])
            else:
                db.execute("UPDATE deploy_targets SET name = %s, type = %s, data = %s, updated_at = %s WHERE id = %s",
                           (data["name"], data["type"], body, now, target_id))
        return self.get_deploy_target(target_id)  # type: ignore[arg-type,return-value]

    def delete_deploy_target(self, target_id: int) -> bool:
        with self._db() as db:
            return db.execute("DELETE FROM deploy_targets WHERE id = %s", (target_id,)).rowcount > 0

    # ------------------------------------------------------------------ 发布记录
    def create_deployment(self, **fields: Any) -> int:
        fields.setdefault("created_at", utcnow())
        fields.setdefault("project_id", self.default_project_id())
        return self._insert("deployments", DEPLOYMENT_FIELDS, fields)

    def create_idempotent_service_deployment(self, **fields: Any) -> Dict[str, Any]:
        """同一发起人、服务与幂等键只创建一次；已存在（包括并发请求先写入）时返回已有记录。"""
        fields.setdefault("created_at", utcnow())
        unknown = set(fields) - set(DEPLOYMENT_FIELDS)
        if unknown:
            raise KeyError(", ".join(sorted(unknown)))
        lookup = ("SELECT * FROM deployments WHERE created_by = %s AND service_id = %s AND idempotency_key = %s")
        key = (fields["created_by"], fields["service_id"], fields["idempotency_key"])
        with self._db() as db:
            existing = db.execute(lookup, key).fetchone()
            if existing:
                return dict(existing)
            cursor = self.attempt(db, f"INSERT INTO deployments({', '.join(fields)}) VALUES ({self.marks(fields)}) "
                                      "RETURNING *", tuple(fields.values()))
            row = cursor.fetchone() if cursor is not None else db.execute(lookup, key).fetchone()
            if row is None:
                raise Conflict("部署请求冲突，请使用新的幂等键")
        return dict(row)

    def update_deployment(self, deployment_id: int, **fields: Any) -> None:
        self._update("deployments", DEPLOYMENT_FIELDS, deployment_id, fields)

    def get_deployment(self, deployment_id: int) -> Optional[Dict[str, Any]]:
        with self._db() as db:
            row = db.execute("SELECT * FROM deployments WHERE id = %s", (deployment_id,)).fetchone()
        return dict(row) if row else None

    def find_deployment_idempotency(self, actor_id: int, service_id: int, key: str) -> Optional[Dict[str, Any]]:
        with self._db() as db:
            row = db.execute("SELECT * FROM deployments WHERE created_by = %s AND service_id = %s "
                             "AND idempotency_key = %s", (actor_id, service_id, key)).fetchone()
        return dict(row) if row else None

    def list_deployments(self, target_id: Optional[int] = None, app_id: Optional[int] = None,
                         limit: int = 30, offset: int = 0,
                         project_ids: Optional[List[int]] = None,
                         namespace_ids: Optional[List[int]] = None, *, project_id: Optional[int] = None,
                         namespace_id: Optional[int] = None, service_id: Optional[int] = None,
                         status: Optional[str] = None) -> Tuple[List[Dict[str, Any]], int]:
        """project_ids / namespace_ids 是可见范围（任一匹配即可见）；均为 None 表示不限范围。"""
        where: List[str] = []
        args: List[Any] = []
        if project_ids is not None or namespace_ids is not None:
            scope = []
            if project_ids:
                scope.append(f"project_id IN ({self.marks(project_ids)})")
                args.extend(project_ids)
            if namespace_ids:
                scope.append(f"namespace_id IN ({self.marks(namespace_ids)})")
                args.extend(namespace_ids)
            if not scope:
                return [], 0
            where.append("(" + " OR ".join(scope) + ")")
        for column, value in (("target_id", target_id or None), ("app_id", app_id or None), ("project_id", project_id),
                              ("namespace_id", namespace_id), ("service_id", service_id), ("status", status)):
            if value is not None:
                where.append(f"{column} = %s")
                args.append(value)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        with self._db() as db:
            total = db.execute(f"SELECT COUNT(*) FROM deployments {clause}", args).fetchone()[0]
            rows = db.execute(f"SELECT * FROM deployments {clause} ORDER BY id DESC LIMIT %s OFFSET %s",
                              (*args, limit, offset)).fetchall()
        return [dict(r) for r in rows], total

    def fail_unfinished_deployments(self, reason: str) -> int:
        """旧部署目标的任务在进程内执行，重启后无法继续。"""
        with self._db() as db:
            return db.execute("UPDATE deployments SET status = 'failed', error = %s, finished_at = %s "
                              "WHERE service_id IS NULL AND status IN ('queued', 'running')",
                              (reason, utcnow())).rowcount

    # ------------------------------------------------------------------ 审批
    def record_approval_transition(self, deployment_id: int, approver_id: int, decision: str, comment: str,
                                   min_approvals: int) -> Dict[str, Any]:
        """在一个事务中记录审批意见并推进状态：拒绝 → rejected；批准数达到要求 → queued。"""
        with self._db() as db:
            deployment = db.execute("SELECT status, created_by FROM deployments WHERE id = %s "
                                    "AND service_id IS NOT NULL FOR UPDATE", (deployment_id,)).fetchone()
            if not deployment:
                raise NotFound("发布任务不存在")
            if deployment["status"] != "pending_approval":
                raise Conflict("发布任务不在待审批状态")
            if str(deployment["created_by"]) == str(approver_id):
                raise Forbidden("发布发起人不能审批自己的任务")
            try:
                db.execute("INSERT INTO approvals(deployment_id, approver_id, decision, comment, created_at) "
                           "VALUES (%s, %s, %s, %s, %s)", (deployment_id, approver_id, decision, comment, utcnow()))
            except Exception as exc:
                if is_unique_violation(exc, APPROVAL_CONSTRAINT):
                    raise Conflict("当前用户已审批该任务") from None
                raise
            if decision == "rejected":
                db.execute("UPDATE deployments SET status = 'rejected', finished_at = %s "
                           "WHERE id = %s AND status = 'pending_approval'", (utcnow(), deployment_id))
            else:
                count = db.execute("SELECT COUNT(*) FROM approvals WHERE deployment_id = %s AND decision = 'approved'",
                                   (deployment_id,)).fetchone()[0]
                if count >= max(1, min_approvals):
                    db.execute("UPDATE deployments SET status = 'queued' WHERE id = %s AND status = 'pending_approval'",
                               (deployment_id,))
            row = db.execute("SELECT * FROM deployments WHERE id = %s", (deployment_id,)).fetchone()
        return dict(row)

    def list_approvals(self, deployment_id: int) -> List[Dict[str, Any]]:
        with self._db() as db:
            rows = db.execute("SELECT a.*, u.username AS approver_username, u.display_name AS approver_display_name "
                              "FROM approvals a LEFT JOIN users u ON u.id = a.approver_id "
                              "WHERE a.deployment_id = %s ORDER BY a.id", (deployment_id,)).fetchall()
        return [dict(row) for row in rows]

    def count_approvals(self, deployment_id: int, decision: str = "approved") -> int:
        with self._db() as db:
            return db.execute("SELECT COUNT(*) FROM approvals WHERE deployment_id = %s AND decision = %s",
                              (deployment_id, decision)).fetchone()[0]

    def expire_pending_approvals(self, cutoff: str) -> int:
        with self._db() as db:
            return db.execute("UPDATE deployments SET status = 'expired', error = 'approval expired after 24 hours', "
                              "finished_at = %s WHERE service_id IS NOT NULL AND status = 'pending_approval' "
                              "AND created_at <= %s", (utcnow(), cutoff)).rowcount

    # ------------------------------------------------------------------ 发布队列与租约
    def claim_next_service_deployment(self, worker_id: str, lease_seconds: int = 900) -> Optional[Dict[str, Any]]:
        """领取最早排队、且该服务没有执行中发布的任务；领取时递增 fencing token。

        别的 worker 同时领到同一行或同一服务的另一行时，条件更新不生效或触发唯一索引，本次返回 None。"""
        with self._db() as db:
            row = db.execute(
                "SELECT id FROM deployments d WHERE d.service_id IS NOT NULL AND d.status = 'queued' "
                "AND NOT EXISTS (SELECT 1 FROM deployments active WHERE active.service_id = d.service_id "
                f"AND active.status IN {ACTIVE}) ORDER BY d.id LIMIT 1").fetchone()
            if not row:
                return None
            cursor = self.attempt(db, "UPDATE deployments SET status = 'applying', started_at = %s, lease_owner = %s, "
                                      "lease_until = %s, fencing_token = COALESCE(fencing_token, 0) + 1 "
                                      "WHERE id = %s AND status = 'queued' RETURNING *",
                                  (utcnow(), worker_id, _later(lease_seconds), row["id"]))
            claimed = cursor.fetchone() if cursor is not None else None
        return dict(claimed) if claimed else None

    def service_deployment_lease_valid(self, deployment_id: int, worker_id: str, fencing_token: int) -> bool:
        with self._db() as db:
            row = db.execute("SELECT 1 FROM deployments WHERE id = %s AND service_id IS NOT NULL AND lease_owner = %s "
                             f"AND fencing_token = %s AND status IN {ACTIVE} AND lease_until > %s",
                             (deployment_id, worker_id, fencing_token, utcnow())).fetchone()
        return row is not None

    def update_service_deployment(self, deployment_id: int, worker_id: str, fencing_token: int, **fields: Any) -> bool:
        """只有仍持有租约（worker_id + fencing token）的 worker 能更新执行中的任务。"""
        unknown = set(fields) - set(DEPLOYMENT_FIELDS)
        if unknown:
            raise KeyError(", ".join(sorted(unknown)))
        if not fields:
            return True
        assignments = ", ".join(f"{key} = %s" for key in fields)
        with self._db() as db:
            cur = db.execute(f"UPDATE deployments SET {assignments} WHERE id = %s AND lease_owner = %s "
                             f"AND fencing_token = %s AND status IN {ACTIVE}",
                             (*fields.values(), deployment_id, worker_id, fencing_token))
        return cur.rowcount == 1

    def renew_service_deployment_lease(self, deployment_id: int, worker_id: str, fencing_token: int,
                                       lease_seconds: int) -> bool:
        """延长仍由该 worker 持有且未过期的租约。"""
        with self._db() as db:
            return db.execute(f"UPDATE deployments SET lease_until = %s WHERE id = %s AND lease_owner = %s "
                              f"AND fencing_token = %s AND status IN {ACTIVE} AND lease_until > %s",
                              (_later(lease_seconds), deployment_id, worker_id, fencing_token, utcnow())).rowcount == 1

    def expire_stale_service_leases(self, reason: str) -> int:
        """租约已过期的执行中任务无法确认集群状态，标记为 unknown 并释放该服务的发布槽。"""
        now = utcnow()
        with self._db() as db:
            return db.execute("UPDATE deployments SET status = 'unknown', error = %s, finished_at = %s, "
                              f"lease_owner = NULL, lease_until = NULL WHERE service_id IS NOT NULL AND status IN {ACTIVE} "
                              "AND lease_until <= %s", (reason, now, now)).rowcount

    def mark_interrupted_service_deployments_unknown(self, reason: str) -> int:
        with self._db() as db:
            return db.execute("UPDATE deployments SET status = 'unknown', error = %s, finished_at = %s, "
                              f"lease_owner = NULL, lease_until = NULL WHERE service_id IS NOT NULL AND status IN {ACTIVE}",
                              (reason, utcnow())).rowcount

    def mark_service_deployment_pending_approval(self, deployment_id: int, worker_id: str, fencing_token: int) -> bool:
        with self._db() as db:
            changed = db.execute("UPDATE deployments SET status = 'pending_approval', lease_owner = NULL, "
                                 "lease_until = NULL WHERE id = %s AND lease_owner = %s AND fencing_token = %s "
                                 f"AND status IN {ACTIVE}", (deployment_id, worker_id, fencing_token)).rowcount
        return changed == 1

    def complete_service_deployment(self, deployment_id: int, worker_id: str, fencing_token: int, service_id: int,
                                    revision_id: int, message: str) -> bool:
        now = utcnow()
        with self._db() as db:
            changed = db.execute("UPDATE deployments SET status = 'success', message = %s, finished_at = %s, "
                                 "lease_owner = NULL, lease_until = NULL WHERE id = %s AND lease_owner = %s "
                                 "AND fencing_token = %s AND status = 'verifying'",
                                 (message, now, deployment_id, worker_id, fencing_token)).rowcount
            if changed:
                db.execute("UPDATE services SET deployed_revision_id = %s, updated_at = %s WHERE id = %s",
                           (revision_id, now, service_id))
        return changed == 1
