"""服务、配置修订与密钥版本。

修订不可变：每次修改配置或设置密钥都会生成新的修订号，服务记录指向当前修订（current_revision_id）
与最近一次成功发布的修订（deployed_revision_id）。生成修订号前先锁住服务行，并发修改按顺序执行。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from ..errors import RevisionConflict
from .schema import utcnow

SERVICE_SELECT = ("SELECT s.*, r.spec_json FROM services s "
                  "LEFT JOIN service_revisions r ON r.id = s.current_revision_id")
REVISION_CONFLICT = "配置已被其他人修改，请刷新后基于最新修订重新编辑"


class ServicesMixin:
    @staticmethod
    def _revision(row: Dict[str, Any]) -> Dict[str, Any]:
        item = dict(row)
        item["spec"] = json.loads(item.pop("spec_json"))
        item["secret_versions"] = json.loads(item.pop("secret_versions_json"))
        return item

    @staticmethod
    def _service(row: Dict[str, Any]) -> Dict[str, Any]:
        item = dict(row)
        spec = item.pop("spec_json", None)
        item["spec"] = json.loads(spec) if spec else None
        return item

    @staticmethod
    def _add_revision(db: Any, service_id: int, spec_json: str, secret_versions_json: str,
                      created_by: int, now: str) -> int:
        """调用前必须已锁住服务行（或服务行在本事务内新建）。"""
        number = db.execute("SELECT COALESCE(MAX(number), 0) + 1 FROM service_revisions WHERE service_id = %s",
                            (service_id,)).fetchone()[0]
        return int(db.execute("INSERT INTO service_revisions(service_id, number, spec_json, secret_versions_json, "
                              "created_by, created_at) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                              (service_id, number, spec_json, secret_versions_json, created_by, now)).fetchone()["id"])

    def get_service(self, service_id: int) -> Optional[Dict[str, Any]]:
        with self._db() as db:
            row = db.execute(SERVICE_SELECT + " WHERE s.id = %s", (service_id,)).fetchone()
        return self._service(row) if row else None

    def list_services(self, project_ids: Optional[List[int]] = None,
                      namespace_ids: Optional[List[int]] = None) -> List[Dict[str, Any]]:
        """不传范围时返回全部；传入范围时返回项目或命名空间任一匹配的服务。"""
        where, args = "", []
        if project_ids is not None or namespace_ids is not None:
            scopes = []
            if project_ids:
                scopes.append(f"s.project_id IN ({self.marks(project_ids)})")
                args.extend(project_ids)
            if namespace_ids:
                scopes.append(f"s.namespace_id IN ({self.marks(namespace_ids)})")
                args.extend(namespace_ids)
            if not scopes:
                return []
            where = " WHERE (" + " OR ".join(scopes) + ")"
        with self._db() as db:
            rows = db.execute(SERVICE_SELECT + where + " ORDER BY lower(s.name), s.id", args).fetchall()
        return [self._service(row) for row in rows]

    def app_has_services(self, app_id: int) -> bool:
        with self._db() as db:
            return db.execute("SELECT 1 FROM services WHERE app_id = %s LIMIT 1", (app_id,)).fetchone() is not None

    def create_service(self, name: str, project_id: int, namespace_id: int, app_id: int, spec: Dict[str, Any],
                       created_by: int) -> Dict[str, Any]:
        now = utcnow()
        with self._db() as db, self._unique("该命名空间中已有同名服务"):
            app = db.execute("SELECT project_id FROM apps WHERE id = %s", (app_id,)).fetchone()
            allocated = db.execute("SELECT 1 FROM project_namespaces WHERE project_id = %s AND namespace_id = %s",
                                   (project_id, namespace_id)).fetchone()
            if not app or app["project_id"] != project_id:
                raise ValueError("应用不属于指定项目")
            if not allocated:
                raise ValueError("项目尚未分配目标命名空间")
            service_id = int(db.execute("INSERT INTO services(name, project_id, namespace_id, app_id, created_at, "
                                        "updated_at) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                                        (name, project_id, namespace_id, app_id, now, now)).fetchone()["id"])
            revision_id = self._add_revision(db, service_id, json.dumps(spec, ensure_ascii=False), "{}", created_by, now)
            db.execute("UPDATE services SET current_revision_id = %s WHERE id = %s", (revision_id, service_id))
        return self.get_service(service_id)  # type: ignore[return-value]

    def update_service_revision(self, service_id: int, base_revision: int, spec: Dict[str, Any],
                                created_by: int) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
        """基于 base_revision 创建新修订；base_revision 不是当前修订时抛 RevisionConflict。"""
        now = utcnow()
        with self._db() as db:
            service = db.execute("SELECT current_revision_id FROM services WHERE id = %s FOR UPDATE",
                                 (service_id,)).fetchone()
            if not service:
                return None
            if service["current_revision_id"] != base_revision:
                raise RevisionConflict(REVISION_CONFLICT)
            previous = db.execute("SELECT secret_versions_json FROM service_revisions WHERE id = %s",
                                  (base_revision,)).fetchone()
            revision_id = self._add_revision(db, service_id, json.dumps(spec, ensure_ascii=False),
                                             previous["secret_versions_json"] if previous else "{}", created_by, now)
            db.execute("UPDATE services SET current_revision_id = %s, updated_at = %s WHERE id = %s",
                       (revision_id, now, service_id))
            row = db.execute("SELECT * FROM service_revisions WHERE id = %s", (revision_id,)).fetchone()
        return self.get_service(service_id), self._revision(row)  # type: ignore[return-value]

    def get_revision(self, service_id: int, revision_id: int) -> Optional[Dict[str, Any]]:
        with self._db() as db:
            row = db.execute("SELECT * FROM service_revisions WHERE id = %s AND service_id = %s",
                             (revision_id, service_id)).fetchone()
        return self._revision(row) if row else None

    def list_revisions(self, service_id: int) -> List[Dict[str, Any]]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM service_revisions WHERE service_id = %s ORDER BY number DESC",
                              (service_id,)).fetchall()
        return [self._revision(row) for row in rows]

    def create_secret_revision(self, service_id: int, key: str, ciphertext: str,
                               created_by: int) -> Optional[Dict[str, Any]]:
        """保存密钥的新版本，并基于当前修订生成引用该版本的新修订。"""
        now = utcnow()
        with self._db() as db:
            service = db.execute("SELECT current_revision_id FROM services WHERE id = %s FOR UPDATE",
                                 (service_id,)).fetchone()
            if not service:
                return None
            current = db.execute("SELECT * FROM service_revisions WHERE id = %s",
                                 (service["current_revision_id"],)).fetchone()
            versions = json.loads(current["secret_versions_json"])
            version = db.execute("SELECT COALESCE(MAX(version), 0) + 1 FROM service_secrets "
                                 "WHERE service_id = %s AND key = %s", (service_id, key)).fetchone()[0]
            db.execute("INSERT INTO service_secrets(service_id, key, version, ciphertext, created_at) "
                       "VALUES (%s, %s, %s, %s, %s)", (service_id, key, version, ciphertext, now))
            versions[key] = version
            revision_id = self._add_revision(db, service_id, current["spec_json"],
                                             json.dumps(versions, ensure_ascii=False), created_by, now)
            db.execute("UPDATE services SET current_revision_id = %s, updated_at = %s WHERE id = %s",
                       (revision_id, now, service_id))
            result = db.execute("SELECT * FROM service_revisions WHERE id = %s", (revision_id,)).fetchone()
        return self._revision(result)

    def delete_service(self, service_id: int) -> bool:
        """有待审批、排队或执行中的发布时不能删除。"""
        with self._db() as db:
            if not db.execute("SELECT id FROM services WHERE id = %s FOR UPDATE", (service_id,)).fetchone():
                return False
            active = db.execute("SELECT EXISTS(SELECT 1 FROM deployments WHERE service_id = %s AND status IN "
                                "('pending_approval','queued','applying','verifying'))", (service_id,)).fetchone()[0]
            if active:
                return False
            db.execute("DELETE FROM service_secrets WHERE service_id = %s", (service_id,))
            db.execute("DELETE FROM service_revisions WHERE service_id = %s", (service_id,))
            return db.execute("DELETE FROM services WHERE id = %s", (service_id,)).rowcount > 0
