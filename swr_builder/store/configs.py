"""环境档案、负载均衡入口、分层配置集与证书。

配置集与证书都按版本保存，版本不可修改。新版本以"基于哪个版本修改"（base_version）为前提，
基线不是当前版本时抛 RevisionConflict，避免两个人同时编辑时互相覆盖。
JSON 字段（profile、spec、entries、metadata）由服务层校验后传入，这里只负责存取。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..errors import Conflict, RevisionConflict
from .schema import utcnow

EDIT_CONFLICT = "内容已被其他人修改，请刷新后基于最新版本重新编辑"


def _checksum(entries: Any) -> str:
    return hashlib.sha256(json.dumps(entries, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


class ConfigMixin:
    # ------------------------------------------------------------------ 负载均衡入口
    @staticmethod
    def _ingress_profile(row: Dict[str, Any]) -> Dict[str, Any]:
        item = dict(row)
        item["spec"] = json.loads(item["spec"])
        return item

    def list_ingress_profiles(self) -> List[Dict[str, Any]]:
        with self._db() as db:
            rows = db.execute("SELECT p.*, (SELECT COUNT(*) FROM environment_profiles e WHERE e.ingress_profile_id = p.id) "
                              "AS environment_count FROM ingress_profiles p ORDER BY lower(p.name), p.id").fetchall()
        return [self._ingress_profile(r) for r in rows]

    def get_ingress_profile(self, profile_id: int) -> Optional[Dict[str, Any]]:
        with self._db() as db:
            row = db.execute("SELECT p.*, (SELECT COUNT(*) FROM environment_profiles e WHERE e.ingress_profile_id = p.id) "
                             "AS environment_count FROM ingress_profiles p WHERE p.id = %s", (profile_id,)).fetchone()
        return self._ingress_profile(row) if row else None

    def create_ingress_profile(self, name: str, description: str, spec: Dict[str, Any], actor_id: int) -> int:
        now = utcnow()
        with self._db() as db, self._unique("负载均衡入口名称已存在"):
            return int(db.execute("INSERT INTO ingress_profiles(name, description, spec, updated_by, created_at, updated_at) "
                                  "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                                  (name, description, json.dumps(spec, ensure_ascii=False), actor_id, now,
                                   now)).fetchone()["id"])

    def update_ingress_profile(self, profile_id: int, base_version: int, name: str, description: str,
                               spec: Dict[str, Any], actor_id: int) -> bool:
        with self._db() as db, self._unique("负载均衡入口名称已存在"):
            row = db.execute("SELECT version FROM ingress_profiles WHERE id = %s FOR UPDATE", (profile_id,)).fetchone()
            if not row:
                return False
            if row["version"] != base_version:
                raise RevisionConflict(EDIT_CONFLICT)
            db.execute("UPDATE ingress_profiles SET name = %s, description = %s, spec = %s, version = version + 1, "
                       "updated_by = %s, updated_at = %s WHERE id = %s",
                       (name, description, json.dumps(spec, ensure_ascii=False), actor_id, utcnow(), profile_id))
        return True

    def delete_ingress_profile(self, profile_id: int) -> bool:
        with self._db() as db:
            if db.execute("SELECT 1 FROM environment_profiles WHERE ingress_profile_id = %s LIMIT 1",
                          (profile_id,)).fetchone():
                raise Conflict("仍有环境使用该负载均衡入口，不能删除")
            return db.execute("DELETE FROM ingress_profiles WHERE id = %s", (profile_id,)).rowcount > 0

    # ------------------------------------------------------------------ 环境档案
    def get_environment_profile(self, namespace_id: int) -> Optional[Dict[str, Any]]:
        with self._db() as db:
            row = db.execute("SELECT * FROM environment_profiles WHERE namespace_id = %s", (namespace_id,)).fetchone()
        if not row:
            return None
        item = dict(row)
        item["profile"] = json.loads(item["profile"])
        return item

    def list_environment_profiles(self) -> Dict[int, Dict[str, Any]]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM environment_profiles").fetchall()
        return {row["namespace_id"]: dict(row, profile=json.loads(row["profile"])) for row in rows}

    def save_environment_profile(self, namespace_id: int, base_version: int, profile: Dict[str, Any],
                                 ingress_profile_id: Optional[int], actor_id: int) -> int:
        """保存环境档案，返回新版本号。尚未保存过档案的环境 base_version 为 0。"""
        body = json.dumps(profile, ensure_ascii=False)
        now = utcnow()
        with self._db() as db:
            # 锁住命名空间行：首次保存时两个请求不会都通过版本检查
            if not db.execute("SELECT id FROM namespaces WHERE id = %s FOR UPDATE", (namespace_id,)).fetchone():
                raise Conflict("环境不存在")
            row = db.execute("SELECT version FROM environment_profiles WHERE namespace_id = %s",
                             (namespace_id,)).fetchone()
            current = row["version"] if row else 0
            if current != base_version:
                raise RevisionConflict(EDIT_CONFLICT)
            if row:
                db.execute("UPDATE environment_profiles SET profile = %s, ingress_profile_id = %s, version = version + 1, "
                           "updated_by = %s, updated_at = %s WHERE namespace_id = %s",
                           (body, ingress_profile_id, actor_id, now, namespace_id))
            else:
                db.execute("INSERT INTO environment_profiles(namespace_id, ingress_profile_id, profile, version, "
                           "updated_by, updated_at) VALUES (%s, %s, %s, 1, %s, %s)",
                           (namespace_id, ingress_profile_id, body, actor_id, now))
        return current + 1

    # ------------------------------------------------------------------ 配置集
    @staticmethod
    def _config_set(row: Dict[str, Any]) -> Dict[str, Any]:
        item = dict(row)
        entries = item.pop("entries", None)
        item["entries"] = json.loads(entries) if entries else []
        return item

    _CONFIG_SELECT = ("SELECT s.*, v.number AS version, v.entries, v.checksum, v.created_at AS version_created_at, "
                      "v.created_by AS version_created_by, v.note AS version_note FROM config_sets s "
                      "LEFT JOIN config_set_versions v ON v.id = s.current_version_id")

    def list_config_sets(self, scopes: Optional[Sequence[Tuple[str, int]]] = None,
                         kind: Optional[str] = None) -> List[Dict[str, Any]]:
        """scopes 为 None 表示全部；否则返回属于任一 (scope_type, scope_id) 的配置集（含当前版本内容）。"""
        where: List[str] = []
        args: List[Any] = []
        if scopes is not None:
            if not scopes:
                return []
            where.append("(" + " OR ".join("(s.scope_type = %s AND s.scope_id = %s)" for _ in scopes) + ")")
            for scope_type, scope_id in scopes:
                args.extend((scope_type, scope_id))
        if kind:
            where.append("s.kind = %s")
            args.append(kind)
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        with self._db() as db:
            rows = db.execute(self._CONFIG_SELECT + clause + " ORDER BY s.scope_type, s.scope_id, s.name", args).fetchall()
        return [self._config_set(r) for r in rows]

    def get_config_set(self, set_id: int) -> Optional[Dict[str, Any]]:
        with self._db() as db:
            row = db.execute(self._CONFIG_SELECT + " WHERE s.id = %s", (set_id,)).fetchone()
        return self._config_set(row) if row else None

    def find_config_set(self, scope_type: str, scope_id: int, name: str) -> Optional[Dict[str, Any]]:
        with self._db() as db:
            row = db.execute(self._CONFIG_SELECT + " WHERE s.scope_type = %s AND s.scope_id = %s AND s.name = %s",
                             (scope_type, scope_id, name)).fetchone()
        return self._config_set(row) if row else None

    @staticmethod
    def _write_version(db: Any, set_id: int, number: int, entries: List[Dict[str, Any]],
                       refs: Sequence[Tuple[str, int, int]], note: str, actor_id: int, now: str) -> int:
        version_id = int(db.execute(
            "INSERT INTO config_set_versions(config_set_id, number, entries, checksum, note, created_by, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (set_id, number, json.dumps(entries, ensure_ascii=False), _checksum(entries), note, actor_id, now)
        ).fetchone()["id"])
        for key, certificate_id, certificate_version_id in refs:
            db.execute("INSERT INTO config_certificate_refs(config_set_version_id, certificate_id, certificate_version_id, "
                       "entry_key) VALUES (%s, %s, %s, %s)", (version_id, certificate_id, certificate_version_id, key))
        db.execute("UPDATE config_sets SET current_version_id = %s, updated_at = %s WHERE id = %s",
                   (version_id, now, set_id))
        return version_id

    def create_config_set(self, scope_type: str, scope_id: int, name: str, kind: str, description: str,
                          entries: List[Dict[str, Any]], refs: Sequence[Tuple[str, int, int]], note: str,
                          actor_id: int) -> int:
        now = utcnow()
        with self._db() as db, self._unique("同一层级下已有同名配置"):
            set_id = int(db.execute(
                "INSERT INTO config_sets(scope_type, scope_id, name, kind, description, created_by, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (scope_type, scope_id, name, kind, description, actor_id, now, now)).fetchone()["id"])
            self._write_version(db, set_id, 1, entries, refs, note, actor_id, now)
        return set_id

    def add_config_version(self, set_id: int, base_version: int, entries: List[Dict[str, Any]],
                           refs: Sequence[Tuple[str, int, int]], note: str, actor_id: int,
                           description: Optional[str] = None) -> Optional[int]:
        """基于 base_version 保存新版本，返回新版本号；配置集不存在时返回 None。"""
        now = utcnow()
        with self._db() as db:
            row = db.execute("SELECT s.id, v.number FROM config_sets s LEFT JOIN config_set_versions v "
                             "ON v.id = s.current_version_id WHERE s.id = %s FOR UPDATE OF s", (set_id,)).fetchone()
            if not row:
                return None
            current = row["number"] or 0
            if current != base_version:
                raise RevisionConflict(EDIT_CONFLICT)
            self._write_version(db, set_id, current + 1, entries, refs, note, actor_id, now)
            if description is not None:
                db.execute("UPDATE config_sets SET description = %s WHERE id = %s", (description, set_id))
        return current + 1

    def list_config_versions(self, set_id: int) -> List[Dict[str, Any]]:
        with self._db() as db:
            rows = db.execute("SELECT v.id, v.number, v.checksum, v.note, v.created_by, v.created_at, u.username "
                              "AS created_by_username FROM config_set_versions v LEFT JOIN users u ON u.id = v.created_by "
                              "WHERE v.config_set_id = %s ORDER BY v.number DESC", (set_id,)).fetchall()
        return [dict(r) for r in rows]

    def get_config_version(self, set_id: int, number: int) -> Optional[Dict[str, Any]]:
        with self._db() as db:
            row = db.execute("SELECT * FROM config_set_versions WHERE config_set_id = %s AND number = %s",
                             (set_id, number)).fetchone()
        if not row:
            return None
        item = dict(row)
        item["entries"] = json.loads(item["entries"])
        return item

    def delete_config_set(self, set_id: int) -> bool:
        with self._db() as db:
            return db.execute("DELETE FROM config_sets WHERE id = %s", (set_id,)).rowcount > 0

    def count_config_sets(self, scope_type: str, scope_id: int) -> int:
        with self._db() as db:
            return db.execute("SELECT COUNT(*) FROM config_sets WHERE scope_type = %s AND scope_id = %s",
                              (scope_type, scope_id)).fetchone()[0]

    # ------------------------------------------------------------------ 证书
    _CERT_SELECT = ("SELECT c.*, v.number AS version, v.filename, v.size, v.sha256, v.metadata, v.not_after, "
                    "v.created_at AS version_created_at, "
                    "(SELECT COUNT(DISTINCT s.id) FROM config_certificate_refs r JOIN config_sets s "
                    " ON s.current_version_id = r.config_set_version_id WHERE r.certificate_id = c.id) AS used_by "
                    "FROM certificates c LEFT JOIN certificate_versions v ON v.id = c.current_version_id")

    @staticmethod
    def _certificate(row: Dict[str, Any]) -> Dict[str, Any]:
        item = dict(row)
        item["metadata"] = json.loads(item["metadata"]) if item.get("metadata") else {}
        return item

    def list_certificates(self) -> List[Dict[str, Any]]:
        with self._db() as db:
            rows = db.execute(self._CERT_SELECT + " ORDER BY lower(c.name), c.id").fetchall()
        return [self._certificate(r) for r in rows]

    def get_certificate(self, certificate_id: int) -> Optional[Dict[str, Any]]:
        with self._db() as db:
            row = db.execute(self._CERT_SELECT + " WHERE c.id = %s", (certificate_id,)).fetchone()
        return self._certificate(row) if row else None

    @staticmethod
    def _write_certificate_version(db: Any, certificate_id: int, number: int, version: Dict[str, Any],
                                   actor_id: int, now: str) -> int:
        version_id = int(db.execute(
            "INSERT INTO certificate_versions(certificate_id, number, filename, ciphertext, size, sha256, metadata, "
            "not_after, note, created_by, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (certificate_id, number, version["filename"], version["ciphertext"], version["size"], version["sha256"],
             json.dumps(version["metadata"], ensure_ascii=False), version.get("not_after"), version.get("note", ""),
             actor_id, now)).fetchone()["id"])
        db.execute("UPDATE certificates SET current_version_id = %s, updated_at = %s WHERE id = %s",
                   (version_id, now, certificate_id))
        return version_id

    def create_certificate(self, name: str, kind: str, description: str, version: Dict[str, Any], actor_id: int) -> int:
        now = utcnow()
        with self._db() as db, self._unique("证书名称已存在"):
            certificate_id = int(db.execute(
                "INSERT INTO certificates(name, kind, description, created_by, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                (name, kind, description, actor_id, now, now)).fetchone()["id"])
            self._write_certificate_version(db, certificate_id, 1, version, actor_id, now)
        return certificate_id

    def add_certificate_version(self, certificate_id: int, base_version: int, kind: str, version: Dict[str, Any],
                                actor_id: int) -> Optional[int]:
        now = utcnow()
        with self._db() as db:
            row = db.execute("SELECT c.id, v.number FROM certificates c LEFT JOIN certificate_versions v "
                             "ON v.id = c.current_version_id WHERE c.id = %s FOR UPDATE OF c", (certificate_id,)).fetchone()
            if not row:
                return None
            current = row["number"] or 0
            if current != base_version:
                raise RevisionConflict(EDIT_CONFLICT)
            self._write_certificate_version(db, certificate_id, current + 1, version, actor_id, now)
            db.execute("UPDATE certificates SET kind = %s WHERE id = %s", (kind, certificate_id))
        return current + 1

    def list_certificate_versions(self, certificate_id: int) -> List[Dict[str, Any]]:
        with self._db() as db:
            rows = db.execute("SELECT v.id, v.number, v.filename, v.size, v.sha256, v.metadata, v.not_after, v.note, "
                              "v.created_by, v.created_at, u.username AS created_by_username FROM certificate_versions v "
                              "LEFT JOIN users u ON u.id = v.created_by WHERE v.certificate_id = %s ORDER BY v.number DESC",
                              (certificate_id,)).fetchall()
        return [self._certificate(r) for r in rows]

    def get_certificate_version(self, certificate_id: int, number: Optional[int] = None,
                                version_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """按版本号或版本 ID 读取（含密文）。"""
        with self._db() as db:
            if version_id is not None:
                row = db.execute("SELECT * FROM certificate_versions WHERE certificate_id = %s AND id = %s",
                                 (certificate_id, version_id)).fetchone()
            else:
                row = db.execute("SELECT * FROM certificate_versions WHERE certificate_id = %s AND number = %s",
                                 (certificate_id, number)).fetchone()
        return self._certificate(row) if row else None

    def certificate_usage(self, certificate_id: int) -> List[Dict[str, Any]]:
        """引用该证书的配置集（只看当前版本）。"""
        with self._db() as db:
            rows = db.execute("SELECT s.id, s.scope_type, s.scope_id, s.name, s.kind, r.entry_key, cv.number "
                              "AS certificate_version FROM config_certificate_refs r JOIN config_sets s "
                              "ON s.current_version_id = r.config_set_version_id JOIN certificate_versions cv "
                              "ON cv.id = r.certificate_version_id WHERE r.certificate_id = %s ORDER BY s.id, r.entry_key",
                              (certificate_id,)).fetchall()
        return [dict(r) for r in rows]

    def delete_certificate(self, certificate_id: int) -> bool:
        with self._db() as db:
            if db.execute("SELECT 1 FROM config_certificate_refs WHERE certificate_id = %s LIMIT 1",
                          (certificate_id,)).fetchone():
                raise Conflict("证书仍被配置（含历史版本）引用，不能删除")
            return db.execute("DELETE FROM certificates WHERE id = %s", (certificate_id,)).rowcount > 0
