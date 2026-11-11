"""旧部署目标、发布记录、审批与发布队列租约。

发布记录状态：pending_approval → queued → applying → verifying → success；
失败时为 failed / rejected / expired / cancelled，外部写入结果无法确认时为 unknown。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from ..errors import Conflict, Forbidden, NotFound
from .schema import DEPLOYMENT_FIELDS, utcnow

ACTIVE = "('applying','verifying')"


def _later(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat(timespec="seconds")


class DeploymentsMixin:
    # ------------------------------------------------------------------ 旧部署目标
    @staticmethod
    def _target(row: sqlite3.Row) -> Dict[str, Any]:
        data = json.loads(row["data"])
        data.update(id=row["id"], name=row["name"], type=row["type"],
                    created_at=row["created_at"], updated_at=row["updated_at"])
        return data

    def list_deploy_targets(self) -> List[Dict[str, Any]]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM deploy_targets ORDER BY name COLLATE NOCASE").fetchall()
        return [self._target(r) for r in rows]

    def get_deploy_target(self, target_id: int) -> Optional[Dict[str, Any]]:
        with self._db() as db:
            row = db.execute("SELECT * FROM deploy_targets WHERE id = ?", (target_id,)).fetchone()
        return self._target(row) if row else None

    def find_deploy_target_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        with self._db() as db:
            row = db.execute("SELECT * FROM deploy_targets WHERE name = ?", (name,)).fetchone()
        return self._target(row) if row else None

    def save_deploy_target(self, data: Dict[str, Any], target_id: Optional[int] = None) -> Dict[str, Any]:
        body = json.dumps({k: v for k, v in data.items()
                           if k not in ("id", "name", "type", "created_at", "updated_at")}, ensure_ascii=False)
        now = utcnow()
        with self._db() as db, self._unique("部署目标名称已存在"):
            if target_id is None:
                target_id = db.execute("INSERT INTO deploy_targets(name, type, data, created_at, updated_at) "
                                       "VALUES (?, ?, ?, ?, ?)", (data["name"], data["type"], body, now, now)).lastrowid
            else:
                db.execute("UPDATE deploy_targets SET name = ?, type = ?, data = ?, updated_at = ? WHERE id = ?",
                           (data["name"], data["type"], body, now, target_id))
        return self.get_deploy_target(target_id)  # type: ignore[arg-type,return-value]

    def delete_deploy_target(self, target_id: int) -> bool:
        with self._db() as db:
            return db.execute("DELETE FROM deploy_targets WHERE id = ?", (target_id,)).rowcount > 0

    # ------------------------------------------------------------------ 发布记录
    def create_deployment(self, **fields: Any) -> int:
        fields.setdefault("created_at", utcnow())
        fields.setdefault("project_id", self.default_project_id())
        return self._insert("deployments", DEPLOYMENT_FIELDS, fields)

    def create_idempotent_service_deployment(self, **fields: Any) -> Dict[str, Any]:
        """同一发起人、服务与幂等键只创建一次；已存在时返回已有记录。"""
        fields.setdefault("created_at", utcnow())
        unknown = set(fields) - set(DEPLOYMENT_FIELDS)
        if unknown:
            raise KeyError(", ".join(sorted(unknown)))
        with self._db() as db, self._unique("部署请求冲突，请使用新的幂等键"):
            existing = db.execute("SELECT * FROM deployments WHERE created_by = ? AND service_id = ? AND idempotency_key = ?",
                                  (fields["created_by"], fields["service_id"], fields["idempotency_key"])).fetchone()
            if existing:
                return dict(existing)
            cur = db.execute(f"INSERT INTO deployments({', '.join(fields)}) VALUES ({', '.join('?' for _ in fields)})",
                             tuple(fields.values()))
            row = db.execute("SELECT * FROM deployments WHERE id = ?", (cur.lastrowid,)).fetchone()
        return dict(row)

    def update_deployment(self, deployment_id: int, **fields: Any) -> None:
        self._update("deployments", DEPLOYMENT_FIELDS, deployment_id, fields)

    def get_deployment(self, deployment_id: int) -> Optional[Dict[str, Any]]:
        with self._db() as db:
            row = db.execute("SELECT * FROM deployments WHERE id = ?", (deployment_id,)).fetchone()
        return dict(row) if row else None

    def find_deployment_idempotency(self, actor_id: int, service_id: int, key: str) -> Optional[Dict[str, Any]]:
        with self._db() as db:
            row = db.execute("SELECT * FROM deployments WHERE created_by = ? AND service_id = ? AND idempotency_key = ?",
                             (actor_id, service_id, key)).fetchone()
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
                scope.append(f"project_id IN ({','.join('?' for _ in project_ids)})")
                args.extend(project_ids)
            if namespace_ids:
                scope.append(f"namespace_id IN ({','.join('?' for _ in namespace_ids)})")
                args.extend(namespace_ids)
            if not scope:
                return [], 0
            where.append("(" + " OR ".join(scope) + ")")
        for column, value in (("target_id", target_id or None), ("app_id", app_id or None), ("project_id", project_id),
                              ("namespace_id", namespace_id), ("service_id", service_id), ("status", status)):
            if value is not None:
                where.append(f"{column} = ?")
                args.append(value)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        with self._db() as db:
            total = db.execute(f"SELECT COUNT(*) FROM deployments {clause}", args).fetchone()[0]
            rows = db.execute(f"SELECT * FROM deployments {clause} ORDER BY id DESC LIMIT ? OFFSET ?",
                              (*args, limit, offset)).fetchall()
        return [dict(r) for r in rows], total

    def fail_unfinished_deployments(self, reason: str) -> int:
        """旧部署目标的任务在进程内执行，重启后无法继续。"""
        with self._db() as db:
            return db.execute("UPDATE deployments SET status = 'failed', error = ?, finished_at = ? "
                              "WHERE service_id IS NULL AND status IN ('queued', 'running')", (reason, utcnow())).rowcount

    # ------------------------------------------------------------------ 审批
    def record_approval_transition(self, deployment_id: int, approver_id: int, decision: str, comment: str,
                                   min_approvals: int) -> Dict[str, Any]:
        """在一个事务中记录审批意见并推进状态：拒绝 → rejected；批准数达到要求 → queued。"""
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            deployment = db.execute("SELECT status, created_by FROM deployments WHERE id = ? AND service_id IS NOT NULL",
                                    (deployment_id,)).fetchone()
            if not deployment:
                raise NotFound("发布任务不存在")
            if deployment["status"] != "pending_approval":
                raise Conflict("发布任务不在待审批状态")
            if str(deployment["created_by"]) == str(approver_id):
                raise Forbidden("发布发起人不能审批自己的任务")
            try:
                db.execute("INSERT INTO approvals(deployment_id, approver_id, decision, comment, created_at) "
                           "VALUES (?, ?, ?, ?, ?)", (deployment_id, approver_id, decision, comment, utcnow()))
            except sqlite3.IntegrityError as exc:
                if "approvals.deployment_id, approvals.approver_id" in str(exc):
                    raise Conflict("当前用户已审批该任务") from None
                raise
            if decision == "rejected":
                db.execute("UPDATE deployments SET status = 'rejected', finished_at = ? "
                           "WHERE id = ? AND status = 'pending_approval'", (utcnow(), deployment_id))
            else:
                count = db.execute("SELECT COUNT(*) FROM approvals WHERE deployment_id = ? AND decision = 'approved'",
                                   (deployment_id,)).fetchone()[0]
                if count >= max(1, min_approvals):
                    db.execute("UPDATE deployments SET status = 'queued' WHERE id = ? AND status = 'pending_approval'",
                               (deployment_id,))
            row = db.execute("SELECT * FROM deployments WHERE id = ?", (deployment_id,)).fetchone()
        return dict(row)

    def list_approvals(self, deployment_id: int) -> List[Dict[str, Any]]:
        with self._db() as db:
            rows = db.execute("SELECT a.*, u.username AS approver_username, u.display_name AS approver_display_name "
                              "FROM approvals a LEFT JOIN users u ON u.id = a.approver_id "
                              "WHERE a.deployment_id = ? ORDER BY a.id", (deployment_id,)).fetchall()
        return [dict(row) for row in rows]

    def count_approvals(self, deployment_id: int, decision: str = "approved") -> int:
        with self._db() as db:
            return db.execute("SELECT COUNT(*) FROM approvals WHERE deployment_id = ? AND decision = ?",
                              (deployment_id, decision)).fetchone()[0]

    def expire_pending_approvals(self, cutoff: str) -> int:
        with self._db() as db:
            return db.execute("UPDATE deployments SET status = 'expired', error = 'approval expired after 24 hours', "
                              "finished_at = ? WHERE service_id IS NOT NULL AND status = 'pending_approval' "
                              "AND created_at <= ?", (utcnow(), cutoff)).rowcount

    # ------------------------------------------------------------------ 发布队列与租约
    def claim_next_service_deployment(self, worker_id: str, lease_seconds: int = 900) -> Optional[Dict[str, Any]]:
        """领取最早排队、且该服务没有执行中发布的任务；领取时递增 fencing token。"""
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM deployments d WHERE d.service_id IS NOT NULL AND d.status = 'queued' "
                f"AND NOT EXISTS (SELECT 1 FROM deployments active WHERE active.service_id = d.service_id "
                f"AND active.status IN {ACTIVE}) ORDER BY d.id LIMIT 1").fetchone()
            if not row:
                return None
            token = (row["fencing_token"] or 0) + 1
            changed = db.execute("UPDATE deployments SET status = 'applying', started_at = ?, lease_owner = ?, "
                                 "lease_until = ?, fencing_token = ? WHERE id = ? AND status = 'queued'",
                                 (utcnow(), worker_id, _later(lease_seconds), token, row["id"])).rowcount
            if not changed:
                return None
            claimed = db.execute("SELECT * FROM deployments WHERE id = ?", (row["id"],)).fetchone()
        return dict(claimed)

    def service_deployment_lease_valid(self, deployment_id: int, worker_id: str, fencing_token: int) -> bool:
        with self._db() as db:
            row = db.execute("SELECT 1 FROM deployments WHERE id = ? AND service_id IS NOT NULL AND lease_owner = ? "
                             f"AND fencing_token = ? AND status IN {ACTIVE} AND lease_until > ?",
                             (deployment_id, worker_id, fencing_token, utcnow())).fetchone()
        return row is not None

    def update_service_deployment(self, deployment_id: int, worker_id: str, fencing_token: int, **fields: Any) -> bool:
        """只有仍持有租约（worker_id + fencing token）的 worker 能更新执行中的任务。"""
        unknown = set(fields) - set(DEPLOYMENT_FIELDS)
        if unknown:
            raise KeyError(", ".join(sorted(unknown)))
        if not fields:
            return True
        assignments = ", ".join(f"{key} = ?" for key in fields)
        with self._db() as db:
            cur = db.execute(f"UPDATE deployments SET {assignments} WHERE id = ? AND lease_owner = ? "
                             f"AND fencing_token = ? AND status IN {ACTIVE}",
                             (*fields.values(), deployment_id, worker_id, fencing_token))
        return cur.rowcount == 1

    def renew_service_deployment_lease(self, deployment_id: int, worker_id: str, fencing_token: int,
                                       lease_seconds: int) -> bool:
        """延长仍由该 worker 持有且未过期的租约。"""
        with self._db() as db:
            return db.execute(f"UPDATE deployments SET lease_until = ? WHERE id = ? AND lease_owner = ? "
                              f"AND fencing_token = ? AND status IN {ACTIVE} AND lease_until > ?",
                              (_later(lease_seconds), deployment_id, worker_id, fencing_token, utcnow())).rowcount == 1

    def expire_stale_service_leases(self, reason: str) -> int:
        """租约已过期的执行中任务无法确认集群状态，标记为 unknown 并释放该服务的发布槽。"""
        now = utcnow()
        with self._db() as db:
            return db.execute("UPDATE deployments SET status = 'unknown', error = ?, finished_at = ?, lease_owner = NULL, "
                              f"lease_until = NULL WHERE service_id IS NOT NULL AND status IN {ACTIVE} "
                              "AND lease_until <= ?", (reason, now, now)).rowcount

    def mark_interrupted_service_deployments_unknown(self, reason: str) -> int:
        with self._db() as db:
            return db.execute("UPDATE deployments SET status = 'unknown', error = ?, finished_at = ?, lease_owner = NULL, "
                              f"lease_until = NULL WHERE service_id IS NOT NULL AND status IN {ACTIVE}",
                              (reason, utcnow())).rowcount

    def mark_service_deployment_pending_approval(self, deployment_id: int, worker_id: str, fencing_token: int) -> bool:
        with self._db() as db:
            changed = db.execute("UPDATE deployments SET status = 'pending_approval', lease_owner = NULL, "
                                 "lease_until = NULL WHERE id = ? AND lease_owner = ? AND fencing_token = ? "
                                 f"AND status IN {ACTIVE}", (deployment_id, worker_id, fencing_token)).rowcount
        return changed == 1

    def complete_service_deployment(self, deployment_id: int, worker_id: str, fencing_token: int, service_id: int,
                                    revision_id: int, message: str) -> bool:
        now = utcnow()
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute("UPDATE deployments SET status = 'success', message = ?, finished_at = ?, "
                                 "lease_owner = NULL, lease_until = NULL WHERE id = ? AND lease_owner = ? "
                                 "AND fencing_token = ? AND status = 'verifying'",
                                 (message, now, deployment_id, worker_id, fencing_token)).rowcount
            if changed:
                db.execute("UPDATE services SET deployed_revision_id = ?, updated_at = ? WHERE id = ?",
                           (revision_id, now, service_id))
        return changed == 1
