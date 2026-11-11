"""把 v1.x 的 SQLite 数据库导入 PostgreSQL / GaussDB。

规则：
- 目标库必须是刚初始化的空库（只有迁移自动创建的默认项目）；
- 在一个事务内按外键顺序复制全部表，保留原 ID，最后重置自增序列并核对每张表的行数，任一步失败整体回滚；
- 原 SQLite 文件只读打开；加密字段原样复制，导入后必须继续使用同一个 secret.key（或 BUILDER_SECRET_KEY）。
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .legacy_sqlite import open_upgraded_copy
from .schema import SERIAL_TABLES

logger = logging.getLogger("swr_builder.store")

# 外键依赖顺序；schema_migrations 不复制（目标库有自己的迁移记录）
TABLE_ORDER = ("projects", "users", "sessions", "api_tokens", "settings", "audit_logs", "apps", "jobs",
               "project_registry_orgs", "custom_roles", "role_bindings", "clusters", "namespaces", "project_namespaces",
               "services", "service_revisions", "service_secrets", "deploy_targets", "deployments", "approvals")
BATCH = 500


def _target_columns(db: Any, table: str) -> Dict[str, str]:
    rows = db.execute("SELECT column_name, data_type FROM information_schema.columns "
                      "WHERE table_schema = current_schema() AND table_name = %s", (table,)).fetchall()
    return {row["column_name"]: row["data_type"] for row in rows}


def _convert(value: Any, data_type: str, warnings: Dict[str, int], label: str) -> Any:
    if value is None:
        return None
    if data_type.startswith("timestamp"):
        text = str(value).strip()
        return text or None
    if data_type == "boolean":
        return bool(int(value)) if isinstance(value, (int, float)) or str(value).isdigit() else bool(value)
    if data_type in ("bigint", "integer", "smallint"):
        if isinstance(value, int):
            return value
        text = str(value).strip()
        if text.lstrip("-").isdigit():
            return int(text)
        warnings[label] = warnings.get(label, 0) + 1
        return None
    return value


def _check_empty(db: Any) -> None:
    for table in TABLE_ORDER:
        count = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        if table == "projects":
            seeded = db.execute("SELECT COUNT(*) FROM projects WHERE slug = 'default'").fetchone()[0]
            if count == seeded <= 1:
                continue
        if count:
            raise RuntimeError(f"目标数据库不是空库（{table} 已有 {count} 行）；请导入到新建的数据库，"
                               "并在导入前不要启动平台")


def import_sqlite(store: Any, sqlite_path: Path) -> Dict[str, Any]:
    """导入并返回 {"tables": {表: 行数}, "warnings": {...}}。"""
    source, workdir = open_upgraded_copy(Path(sqlite_path))
    copied: Dict[str, int] = {}
    warnings: Dict[str, int] = {}
    try:
        with store._db() as db:
            _check_empty(db)
            db.execute("DELETE FROM projects")
            for table in TABLE_ORDER:
                columns = _target_columns(db, table)
                source_columns = [r["name"] for r in source.execute(f"PRAGMA table_info({table})").fetchall()]
                names = [c for c in source_columns if c in columns]
                dropped = sorted(set(source_columns) - set(columns))
                if dropped:
                    logger.info("导入 %s 时忽略旧列 %s", table, ", ".join(dropped))
                insert = (f"INSERT INTO {table}({', '.join(names)}) VALUES ({', '.join('%s' for _ in names)})")
                cursor = db.cursor()
                batch: List[Tuple[Any, ...]] = []
                count = 0
                for row in source.execute(f"SELECT {', '.join(names)} FROM {table}"):
                    batch.append(tuple(_convert(row[n], columns[n], warnings, f"{table}.{n}") for n in names))
                    if len(batch) >= BATCH:
                        cursor.executemany(insert, batch)
                        count += len(batch)
                        batch = []
                if batch:
                    cursor.executemany(insert, batch)
                    count += len(batch)
                expected = source.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                actual = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                if actual != expected or count != expected:
                    raise RuntimeError(f"{table} 行数不一致：SQLite {expected}，导入 {actual}")
                copied[table] = actual
            if not db.execute("SELECT COUNT(*) FROM projects WHERE slug = 'default'").fetchone()[0]:
                raise RuntimeError("SQLite 数据库缺少默认项目（slug = default），无法导入")
            for table in SERIAL_TABLES:
                db.execute(f"SELECT setval('{table}_id_seq', COALESCE((SELECT MAX(id) FROM {table}), 0) + 1, false)")
    finally:
        source.close()
        shutil.rmtree(workdir, ignore_errors=True)
    if warnings:
        logger.warning("以下列有无法转换为数字的旧值，已置空：%s", warnings)
    return {"tables": copied, "warnings": warnings}


def describe(result: Dict[str, Any], target: Optional[str] = None) -> str:
    lines = [f"已导入到 {target}" if target else "导入完成"]
    for table, count in result["tables"].items():
        lines.append(f"  {table}: {count}")
    for label, count in result["warnings"].items():
        lines.append(f"  注意：{label} 有 {count} 个非数字旧值已置空")
    return "\n".join(lines)
