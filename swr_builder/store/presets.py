"""环境预置的数据访问层（store mixin）。"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from .db import Database
from .schema import utcnow


class PresetsMixin:
    """环境预置 mixin"""

    @staticmethod
    def _preset(row: Dict[str, Any]) -> Dict[str, Any]:
        """行转换"""
        item = dict(row)
        for key in ("profile_template", "default_config_sets", "required_resource_types", "required_params"):
            if key in item and item[key]:
                item[key] = json.loads(item[key])
        return item

    def create_preset(self: Database, name: str, description: str, profile_template: Dict[str, Any],
                     default_config_sets: List[Dict[str, Any]], required_resource_types: List[str],
                     required_params: List[Dict[str, str]], actor_id: int) -> int:
        """创建环境预置，返回预置 ID"""
        now = utcnow()

        with self._db() as db, self._unique("已有同名预置"):
            preset_id = int(db.execute(
                "INSERT INTO environment_presets(name, description, profile_template, default_config_sets, "
                "required_resource_types, required_params, created_by, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (name, description,
                 json.dumps(profile_template, ensure_ascii=False),
                 json.dumps(default_config_sets, ensure_ascii=False),
                 json.dumps(required_resource_types, ensure_ascii=False),
                 json.dumps(required_params, ensure_ascii=False),
                 actor_id, now, now)
            ).fetchone()["id"])

        return preset_id

    def list_presets(self: Database) -> List[Dict[str, Any]]:
        """列出所有环境预置"""
        with self._db() as db:
            rows = db.execute(
                "SELECT * FROM environment_presets ORDER BY name"
            ).fetchall()

        return [self._preset(row) for row in rows]

    def get_preset(self: Database, preset_id: int) -> Optional[Dict[str, Any]]:
        """获取环境预置"""
        with self._db() as db:
            row = db.execute(
                "SELECT * FROM environment_presets WHERE id = %s",
                (preset_id,)
            ).fetchone()

        return self._preset(row) if row else None

    def find_preset(self: Database, name: str) -> Optional[Dict[str, Any]]:
        """按名称查找环境预置"""
        with self._db() as db:
            row = db.execute(
                "SELECT * FROM environment_presets WHERE name = %s",
                (name,)
            ).fetchone()

        return self._preset(row) if row else None

    def update_preset(self: Database, preset_id: int, name: str, description: str,
                     profile_template: Dict[str, Any], default_config_sets: List[Dict[str, Any]],
                     required_resource_types: List[str], required_params: List[Dict[str, str]],
                     actor_id: int) -> bool:
        """更新环境预置"""
        now = utcnow()

        with self._db() as db, self._unique("已有同名预置", "uq_environment_presets_name"):
            rowcount = db.execute(
                "UPDATE environment_presets SET name = %s, description = %s, profile_template = %s, "
                "default_config_sets = %s, required_resource_types = %s, required_params = %s, "
                "created_by = %s, updated_at = %s WHERE id = %s",
                (name, description,
                 json.dumps(profile_template, ensure_ascii=False),
                 json.dumps(default_config_sets, ensure_ascii=False),
                 json.dumps(required_resource_types, ensure_ascii=False),
                 json.dumps(required_params, ensure_ascii=False),
                 actor_id, now, preset_id)
            ).rowcount

        return rowcount > 0

    def delete_preset(self: Database, preset_id: int) -> bool:
        """删除环境预置"""
        with self._db() as db:
            return db.execute("DELETE FROM environment_presets WHERE id = %s", (preset_id,)).rowcount > 0

    def apply_preset_to_environment(self: Database, namespace_id: int, preset: Dict[str, Any],
                                   actor_id: int) -> None:
        """
        应用预置到环境：复制 profile_template 到 environment_profiles，
        创建 default_config_sets，写入 required_params。

        此方法不校验权限、不校验预置是否存在，由调用方负责。
        在一个事务中完成所有操作。
        """
        from . import configs  # 避免循环导入

        now = utcnow()
        profile_json = json.dumps(preset["profile_template"], ensure_ascii=False)
        required_params_json = json.dumps(preset["required_params"], ensure_ascii=False)

        with self._db() as db:
            # 检查命名空间是否存在
            if not db.execute("SELECT id FROM namespaces WHERE id = %s FOR UPDATE", (namespace_id,)).fetchone():
                raise ValueError("环境不存在")

            # 更新或插入 environment_profiles
            row = db.execute("SELECT version FROM environment_profiles WHERE namespace_id = %s",
                           (namespace_id,)).fetchone()

            if row:
                db.execute(
                    "UPDATE environment_profiles SET profile = %s, required_params = %s, version = version + 1, "
                    "updated_by = %s, updated_at = %s WHERE namespace_id = %s",
                    (profile_json, required_params_json, actor_id, now, namespace_id)
                )
            else:
                db.execute(
                    "INSERT INTO environment_profiles(namespace_id, profile, required_params, version, "
                    "updated_by, updated_at) VALUES (%s, %s, %s, 1, %s, %s)",
                    (namespace_id, profile_json, required_params_json, actor_id, now)
                )

            # 创建配置集（通过 configs mixin）
            for config_set in preset.get("default_config_sets") or []:
                kind = config_set["kind"]
                name = config_set["name"]
                entries = config_set.get("entries") or []

                # 使用 configs._write_version 的模式直接插入
                checksum = ""  # 预置中的配置集不需要校验和

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

                db.execute("UPDATE config_sets SET current_version_id = %s WHERE id = %s",
                          (version_id, set_id))
