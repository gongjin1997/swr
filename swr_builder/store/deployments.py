"""部署目标（旧）、发布记录、审批与发布队列租约。"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from .schema import (DEPLOYMENT_FIELDS, utcnow)


class DeploymentsMixin:
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
        with self._db() as db:
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

    def create_deployment(self, **fields: Any) -> int:
        fields.setdefault("created_at", utcnow())
        fields.setdefault("project_id",self.default_project_id())
        return self._insert("deployments", DEPLOYMENT_FIELDS, fields)

    def create_idempotent_service_deployment(self, **fields: Any) -> Dict[str,Any]:
        fields.setdefault('created_at',utcnow())
        allowed=set(DEPLOYMENT_FIELDS)
        if set(fields)-allowed:raise KeyError(', '.join(sorted(set(fields)-allowed)))
        with self._db() as db:
            existing=db.execute('SELECT * FROM deployments WHERE created_by=? AND service_id=? AND idempotency_key=?',
                (fields['created_by'],fields['service_id'],fields['idempotency_key'])).fetchone()
            if existing:return dict(existing)
            cols=', '.join(fields);marks=', '.join('?' for _ in fields)
            cur=db.execute(f'INSERT INTO deployments({cols}) VALUES({marks})',tuple(fields.values()))
            row=db.execute('SELECT * FROM deployments WHERE id=?',(cur.lastrowid,)).fetchone()
        return dict(row)

    def record_approval_transition(self,deployment_id: int,approver_id: int,decision: str,comment: str,
                                   min_approvals: int) -> Dict[str,Any]:
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            deployment=db.execute('SELECT status,created_by FROM deployments WHERE id=? AND service_id IS NOT NULL',
                (deployment_id,)).fetchone()
            if not deployment:raise LookupError('deployment not found')
            if deployment['status']!='pending_approval':raise RuntimeError('deployment is not pending approval')
            if str(deployment['created_by'])==str(approver_id):raise PermissionError('submitter cannot approve own deployment')
            try:
                db.execute('INSERT INTO approvals(deployment_id,approver_id,decision,comment,created_at) VALUES(?,?,?,?,?)',
                    (deployment_id,approver_id,decision,comment,utcnow()))
            except sqlite3.IntegrityError as exc:
                if 'approvals.deployment_id, approvals.approver_id' in str(exc):raise FileExistsError('duplicate approval') from None
                raise
            if decision=='rejected':
                db.execute("UPDATE deployments SET status='rejected',finished_at=? WHERE id=? AND status='pending_approval'",
                    (utcnow(),deployment_id))
            else:
                count=db.execute("SELECT count(*) FROM approvals WHERE deployment_id=? AND decision='approved'",
                    (deployment_id,)).fetchone()[0]
                if count>=max(1,min_approvals):
                    db.execute("UPDATE deployments SET status='queued' WHERE id=? AND status='pending_approval'",(deployment_id,))
            row=db.execute('SELECT * FROM deployments WHERE id=?',(deployment_id,)).fetchone()
        return dict(row)

    def update_deployment(self, deployment_id: int, **fields: Any) -> None:
        self._update("deployments", DEPLOYMENT_FIELDS, deployment_id, fields)

    def claim_next_service_deployment(self,worker_id: str,lease_seconds: int=900) -> Optional[Dict[str,Any]]:
        lease_until=(datetime.now(timezone.utc)+timedelta(seconds=lease_seconds)).isoformat(timespec='seconds')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute("SELECT * FROM deployments d WHERE d.service_id IS NOT NULL AND d.status='queued' "
                "AND NOT EXISTS(SELECT 1 FROM deployments active WHERE active.service_id=d.service_id AND active.status IN ('applying','verifying')) "
                "ORDER BY d.id LIMIT 1").fetchone()
            if not row:return None
            token=(row['fencing_token'] or 0)+1
            changed=db.execute("UPDATE deployments SET status='applying',started_at=?,lease_owner=?,lease_until=?,fencing_token=? WHERE id=? AND status='queued'",
                (utcnow(),worker_id,lease_until,token,row['id'])).rowcount
            if not changed:return None
            claimed=db.execute('SELECT * FROM deployments WHERE id=?',(row['id'],)).fetchone()
        return dict(claimed)

    def service_deployment_lease_valid(self,deployment_id: int,worker_id: str,fencing_token: int) -> bool:
        with self._db() as db:
            row=db.execute("SELECT 1 FROM deployments WHERE id=? AND service_id IS NOT NULL AND lease_owner=? AND fencing_token=? AND status IN ('applying','verifying') AND lease_until>?",
                (deployment_id,worker_id,fencing_token,utcnow())).fetchone()
        return row is not None

    def update_service_deployment(self,deployment_id: int,worker_id: str,fencing_token: int,**fields: Any) -> bool:
        unknown=set(fields)-set(DEPLOYMENT_FIELDS)
        if unknown:raise KeyError(', '.join(sorted(unknown)))
        if not fields:return True
        assignments=', '.join(f'{key}=?' for key in fields)
        with self._db() as db:
            cur=db.execute(f"UPDATE deployments SET {assignments} WHERE id=? AND lease_owner=? AND fencing_token=? AND status IN ('applying','verifying')",
                (*fields.values(),deployment_id,worker_id,fencing_token))
        return cur.rowcount==1

    def renew_service_deployment_lease(self, deployment_id: int, worker_id: str, fencing_token: int,
                                       lease_seconds: int) -> bool:
        """延长仍由该 worker 持有且未过期的租约。"""
        now = datetime.now(timezone.utc)
        lease_until = (now + timedelta(seconds=lease_seconds)).isoformat(timespec='seconds')
        with self._db() as db:
            return db.execute("UPDATE deployments SET lease_until=? WHERE id=? AND lease_owner=? AND fencing_token=? "
                              "AND status IN ('applying','verifying') AND lease_until>?",
                              (lease_until, deployment_id, worker_id, fencing_token,
                               now.isoformat(timespec='seconds'))).rowcount == 1

    def expire_stale_service_leases(self, reason: str) -> int:
        """租约已过期的执行中任务无法确认集群状态，标记为 unknown 并释放该服务的发布槽。"""
        with self._db() as db:
            return db.execute("UPDATE deployments SET status='unknown',error=?,finished_at=?,lease_owner=NULL,lease_until=NULL "
                              "WHERE service_id IS NOT NULL AND status IN ('applying','verifying') AND lease_until<=?",
                              (reason, utcnow(), utcnow())).rowcount

    def mark_interrupted_service_deployments_unknown(self,reason: str) -> int:
        with self._db() as db:
            return db.execute("UPDATE deployments SET status='unknown',error=?,finished_at=?,lease_owner=NULL,lease_until=NULL WHERE service_id IS NOT NULL AND status IN ('applying','verifying')",
                (reason,utcnow())).rowcount

    def mark_service_deployment_pending_approval(self,deployment_id: int,worker_id: str,fencing_token: int) -> bool:
        with self._db() as db:
            changed=db.execute("UPDATE deployments SET status='pending_approval',lease_owner=NULL,lease_until=NULL "
                "WHERE id=? AND lease_owner=? AND fencing_token=? AND status IN ('applying','verifying')",
                (deployment_id,worker_id,fencing_token)).rowcount
        return changed==1

    def complete_service_deployment(self,deployment_id: int,worker_id: str,fencing_token: int,service_id: int,
                                    revision_id: int,message: str) -> bool:
        now=utcnow()
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute("SELECT 1 FROM deployments WHERE id=? AND lease_owner=? AND fencing_token=? AND status='verifying'",
                (deployment_id,worker_id,fencing_token)).fetchone()
            if not row:return False
            changed=db.execute("UPDATE deployments SET status='success',message=?,finished_at=?,lease_owner=NULL,lease_until=NULL WHERE id=? AND lease_owner=? AND fencing_token=? AND status='verifying'",
                (message,now,deployment_id,worker_id,fencing_token)).rowcount
            if changed:
                db.execute('UPDATE services SET deployed_revision_id=?,updated_at=? WHERE id=?',(revision_id,now,service_id))
            return changed==1

    def expire_pending_approvals(self,cutoff: str) -> int:
        with self._db() as db:
            return db.execute("UPDATE deployments SET status='expired',error='approval expired after 24 hours',finished_at=? "
                "WHERE service_id IS NOT NULL AND status='pending_approval' AND created_at<=?",(utcnow(),cutoff)).rowcount

    def get_deployment(self, deployment_id: int) -> Optional[Dict[str, Any]]:
        with self._db() as db:
            row = db.execute("SELECT * FROM deployments WHERE id = ?", (deployment_id,)).fetchone()
        return dict(row) if row else None

    def find_deployment_idempotency(self, actor_id: int, service_id: int, key: str) -> Optional[Dict[str, Any]]:
        with self._db() as db:
            row=db.execute('SELECT * FROM deployments WHERE created_by=? AND service_id=? AND idempotency_key=?',
                (actor_id,service_id,key)).fetchone()
        return dict(row) if row else None

    def list_approvals(self,deployment_id: int) -> List[Dict[str,Any]]:
        with self._db() as db:
            rows=db.execute('SELECT * FROM approvals WHERE deployment_id=? ORDER BY id',(deployment_id,)).fetchall()
        return [dict(row) for row in rows]

    def count_approvals(self,deployment_id: int,decision: str='approved') -> int:
        with self._db() as db:
            return db.execute('SELECT count(*) FROM approvals WHERE deployment_id=? AND decision=?',
                (deployment_id,decision)).fetchone()[0]

    def list_deployments(self, target_id: Optional[int] = None, app_id: Optional[int] = None,
                         limit: int = 30, offset: int = 0,
                         project_ids: Optional[List[int]] = None,
                         namespace_ids: Optional[List[int]] = None, *, project_id=None, namespace_id=None,
                         service_id=None, status=None) -> Tuple[List[Dict[str, Any]], int]:
        where, args = [], []
        if project_ids is not None or namespace_ids is not None:
            project_ids=project_ids or []
            namespace_ids=namespace_ids or []
            if not project_ids and not namespace_ids:
                return [], 0
            scope=[]
            if project_ids:
                scope.append(f"project_id IN ({','.join('?' for _ in project_ids)})");args.extend(project_ids)
            if namespace_ids:
                scope.append(f"namespace_id IN ({','.join('?' for _ in namespace_ids)})");args.extend(namespace_ids)
            where.append('('+' OR '.join(scope)+')')
        if target_id:
            where.append("target_id = ?")
            args.append(target_id)
        if app_id:
            where.append("app_id = ?")
            args.append(app_id)
        for key, value in (("project_id", project_id), ("namespace_id", namespace_id), ("service_id", service_id), ("status", status)):
            if value is not None:
                where.append(f"{key} = ?")
                args.append(value)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        with self._db() as db:
            total = db.execute(f"SELECT COUNT(*) FROM deployments {clause}", args).fetchone()[0]
            rows = db.execute(f"SELECT * FROM deployments {clause} ORDER BY id DESC LIMIT ? OFFSET ?",
                              (*args, limit, offset)).fetchall()
        return [dict(r) for r in rows], total

    def fail_unfinished_deployments(self, reason: str) -> int:
        with self._db() as db:
            return db.execute("UPDATE deployments SET status = 'failed', error = ?, finished_at = ? "
                              "WHERE service_id IS NULL AND status IN ('queued', 'running')", (reason, utcnow())).rowcount
