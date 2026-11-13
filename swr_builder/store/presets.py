"""环境预置的数据访问（每个方法一个事务）。

预置 = 档案模板 + 默认环境层配置集（只有 env 与 files，不保存敏感值）。
套用时复制：档案覆盖为模板（带 base_version），同名配置集已存在时跳过不覆盖。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from ..errors import RevisionConflict
from .db import Database
from .schema import utcnow

EDIT_CONFLICT = "预置已被他人修改，请刷新后重试"


class PresetsMixin:
    @staticmethod
    def _preset(row: Dict[str, Any]) -> Dict[str, Any]:
        item = dict(row)
        for key in ("profile_template", "default_config_sets"):
            if key in item and item[key]:
                item[key] = json.loads(item[key])
        return item

    def create_preset(self: Database, name: str, description: str, profile_template: Dict[str, Any],
                     default_config_sets: List[Dict[str, Any]], actor_id: int) -> int:
        now = utcnow()
        with self._db() as db, self._unique("已有同名预置"):
            preset_id = int(db.execute(
                "INSERT INTO environment_presets(name, description, profile_template, default_config_sets, version, "
                "created_by, created_at, updated_at) VALUES (%s, %s, %s, %s, 1, %s, %s, %s) RETURNING id",
                (name, description, json.dumps(profile_template, ensure_ascii=False),
                 json.dumps(default_config_sets, ensure_ascii=False), actor_id, now, now)
            ).fetchone()["id"])
        return preset_id

    def list_presets(self: Database) -> List[Dict[str, Any]]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM environment_presets WHERE deleted_at IS NULL ORDER BY name").fetchall()
        return [self._preset(row) for row in rows]

    def get_preset(self: Database, preset_id: int) -> Optional[Dict[str, Any]]:
        with self._db() as db:
            row = db.execute("SELECT * FROM environment_presets WHERE id = %s AND deleted_at IS NULL", (preset_id,)).fetchone()
        return self._preset(row) if row else None

    def find_preset(self: Database, name: str) -> Optional[Dict[str, Any]]:
        with self._db() as db:
            row = db.execute("SELECT * FROM environment_presets WHERE name = %s AND deleted_at IS NULL", (name,)).fetchone()
        return self._preset(row) if row else None

    def update_preset(self: Database, preset_id: int, base_version: int, name: str, description: str,
                     profile_template: Dict[str, Any], default_config_sets: List[Dict[str, Any]],
                     actor_id: int) -> Optional[int]:
        """更新预置，返回新版本号；base_version 不匹配时抛 RevisionConflict。"""
        now = utcnow()
        with self._db() as db, self._unique("已有同名预置", "uq_environment_presets_name"):
            row = db.execute("SELECT version FROM environment_presets WHERE id = %s AND deleted_at IS NULL FOR UPDATE",
                           (preset_id,)).fetchone()
            if not row:
                return None
            if row["version"] != base_version:
                raise RevisionConflict(EDIT_CONFLICT)
            db.execute(
                "UPDATE environment_presets SET name = %s, description = %s, profile_template = %s, "
                "default_config_sets = %s, version = version + 1, updated_by = %s, updated_at = %s "
                "WHERE id = %s AND deleted_at IS NULL",
                (name, description, json.dumps(profile_template, ensure_ascii=False),
                 json.dumps(default_config_sets, ensure_ascii=False), actor_id, now, preset_id)
            )
        return base_version + 1

    def delete_preset(self: Database, preset_id: int) -> bool:
        with self._db() as db:
            return db.execute("UPDATE environment_presets SET deleted_at = %s WHERE id = %s AND deleted_at IS NULL",
                              (utcnow(), preset_id)).rowcount > 0

    def apply_preset_to_environment(self: Database, namespace_id: int, preset: Dict[str, Any], actor_id: int) -> None:
        """应用预置到环境：覆盖档案（带 base_version），跳过已存在的同名配置集。一个事务完成。"""
        from .configs import _checksum
        now = utcnow()
        profile_json = json.dumps(preset["profile_template"], ensure_ascii=False)
        with self._db() as db:
            ns = db.execute("SELECT id FROM namespaces WHERE id = %s FOR UPDATE", (namespace_id,)).fetchone()
            if not ns:
                raise ValueError("环境不存在")
            row = db.execute("SELECT version, ingress_profile_id FROM environment_profiles WHERE namespace_id = %s",
                           (namespace_id,)).fetchone()
            current_version = row["version"] if row else 0
            ingress_profile_id = row["ingress_profile_id"] if row else None
            if row:
                db.execute(
                    "UPDATE environment_profiles SET profile = %s, version = version + 1, updated_by = %s, "
                    "updated_at = %s WHERE namespace_id = %s",
                    (profile_json, actor_id, now, namespace_id)
                )
            else:
                db.execute(
                    "INSERT INTO environment_profiles(namespace_id, profile, ingress_profile_id, version, "
                    "updated_by, updated_at) VALUES (%s, %s, %s, 1, %s, %s)",
                    (namespace_id, profile_json, ingress_profile_id, actor_id, now)
                )
            for config_set in preset.get("default_config_sets") or []:
                kind = config_set["kind"]
                name = config_set["name"]
                entries = config_set.get("entries") or []
                existing = db.execute(
                    "SELECT id FROM config_sets WHERE scope_type = %s AND scope_id = %s AND name = %s "
                    "AND deleted_at IS NULL",
                    ("environment", namespace_id, name)
                ).fetchone()
                if existing:
                    continue
                checksum = _checksum(entries)
                set_id = int(db.execute(
                    "INSERT INTO config_sets(scope_type, scope_id, name, kind, description, created_by, "
                    "created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                    ("environment", namespace_id, name, kind, "", actor_id, now, now)
                ).fetchone()["id"])
                version_id = int(db.execute(
                    "INSERT INTO config_set_versions(config_set_id, number, entries, checksum, note, "
                    "created_by, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
                    (set_id, 1, json.dumps(entries, ensure_ascii=False), checksum, "从预置创建", actor_id, now)
                ).fetchone()["id"])
                db.execute("UPDATE config_sets SET current_version_id = %s WHERE id = %s", (version_id, set_id))
