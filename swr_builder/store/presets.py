"""环境预置存储层：environment_presets 表的 CRUD。"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from .schema import utcnow


class PresetsMixin:
    """环境预置的数据库操作，混入 Store"""

    def create_preset(self, name: str, profile_template: Dict[str, Any], config_defaults: List[Dict[str, Any]], *,
                      required_resources: Optional[List[str]] = None, required_params: Optional[List[str]] = None,
                      description: str = "", created_by: Optional[int] = None) -> Dict[str, Any]:
        """创建环境预置"""
        now = utcnow()
        with self._db() as db:
            preset_id = db.execute(
                """INSERT INTO environment_presets(name, description, profile_template, config_defaults,
                                                    required_resources, required_params, created_by, created_at, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (name, description, json.dumps(profile_template, ensure_ascii=False),
                 json.dumps(config_defaults, ensure_ascii=False),
                 json.dumps(required_resources or [], ensure_ascii=False),
                 json.dumps(required_params or [], ensure_ascii=False),
                 created_by, now, now)
            ).fetchone()[0]
            return self.get_preset(preset_id)

    def get_preset(self, preset_id: int) -> Optional[Dict[str, Any]]:
        """获取预置详情"""
        with self._db() as db:
            row = db.execute(
                """SELECT id, name, description, profile_template, config_defaults,
                          required_resources, required_params, created_by, created_at, updated_at
                   FROM environment_presets
                   WHERE id = %s""",
                (preset_id,)
            ).fetchone()
            if not row:
                return None
            return {
                "id": row["id"],
                "name": row["name"],
                "description": row["description"],
                "profile_template": json.loads(row["profile_template"]),
                "config_defaults": json.loads(row["config_defaults"]),
                "required_resources": json.loads(row["required_resources"]),
                "required_params": json.loads(row["required_params"]),
                "created_by": row["created_by"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }

    def get_preset_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """按名称获取预置"""
        with self._db() as db:
            row = db.execute(
                """SELECT id, name, description, profile_template, config_defaults,
                          required_resources, required_params, created_by, created_at, updated_at
                   FROM environment_presets
                   WHERE name = %s""",
                (name,)
            ).fetchone()
            if not row:
                return None
            return {
                "id": row["id"],
                "name": row["name"],
                "description": row["description"],
                "profile_template": json.loads(row["profile_template"]),
                "config_defaults": json.loads(row["config_defaults"]),
                "required_resources": json.loads(row["required_resources"]),
                "required_params": json.loads(row["required_params"]),
                "created_by": row["created_by"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }

    def list_presets(self, limit: int = 100, offset: int = 0) -> tuple[List[Dict[str, Any]], int]:
        """列出预置，返回 (items, total)"""
        with self._db() as db:
            total = db.execute("SELECT COUNT(*) FROM environment_presets").fetchone()[0]
            rows = db.execute(
                """SELECT id, name, description, created_by, created_at, updated_at
                   FROM environment_presets
                   ORDER BY id
                   LIMIT %s OFFSET %s""",
                (limit, offset)
            ).fetchall()
            items = [dict(row) for row in rows]
            return items, total

    def update_preset(self, preset_id: int, *, name: Optional[str] = None, description: Optional[str] = None,
                      profile_template: Optional[Dict[str, Any]] = None,
                      config_defaults: Optional[List[Dict[str, Any]]] = None,
                      required_resources: Optional[List[str]] = None,
                      required_params: Optional[List[str]] = None) -> Dict[str, Any]:
        """更新预置"""
        now = utcnow()
        with self._db() as db:
            preset = db.execute("SELECT * FROM environment_presets WHERE id = %s", (preset_id,)).fetchone()
            if not preset:
                raise ValueError(f"预置不存在: {preset_id}")

            updates = []
            params = []
            if name is not None:
                updates.append("name = %s")
                params.append(name)
            if description is not None:
                updates.append("description = %s")
                params.append(description)
            if profile_template is not None:
                updates.append("profile_template = %s")
                params.append(json.dumps(profile_template, ensure_ascii=False))
            if config_defaults is not None:
                updates.append("config_defaults = %s")
                params.append(json.dumps(config_defaults, ensure_ascii=False))
            if required_resources is not None:
                updates.append("required_resources = %s")
                params.append(json.dumps(required_resources, ensure_ascii=False))
            if required_params is not None:
                updates.append("required_params = %s")
                params.append(json.dumps(required_params, ensure_ascii=False))

            if updates:
                updates.append("updated_at = %s")
                params.append(now)
                params.append(preset_id)
                db.execute(f"UPDATE environment_presets SET {', '.join(updates)} WHERE id = %s", params)

            return self.get_preset(preset_id)

    def delete_preset(self, preset_id: int) -> bool:
        """删除预置"""
        with self._db() as db:
            result = db.execute("DELETE FROM environment_presets WHERE id = %s", (preset_id,))
            return result.rowcount > 0
