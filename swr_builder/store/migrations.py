"""版本化 schema 迁移（PostgreSQL / GaussDB）。

每个迁移只写自己的变更；版本检查、校验和、加锁、事务和登记由 `apply_migrations` 统一处理。
已发布迁移的版本号、名称（校验和 = sha256(名称)）和内容不可修改。数据库中有程序不认识的更高版本时拒绝启动，
避免旧程序在新结构上写数据。

v1.x 的 SQLite 迁移保存在 legacy_sqlite.py，只用于 `db import-sqlite`。
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any, Callable, List, Tuple

from .schema import BASELINE, utcnow

logger = logging.getLogger("swr_builder.store")

Migration = Tuple[int, str, Callable[[Any], None]]


def _baseline(db: Any) -> None:
    for statement in BASELINE:
        db.execute(statement)
    now = utcnow()
    db.execute("INSERT INTO projects(slug, name, created_at, updated_at) VALUES ('default', '默认项目', %s, %s)",
               (now, now))


MIGRATIONS: List[Migration] = [
    (1, "v2-postgresql-baseline", _baseline),
]


def _checksum(name: str) -> str:
    return hashlib.sha256(name.encode("ascii")).hexdigest()


def apply_migrations(database: Any, migrations: List[Migration] = MIGRATIONS) -> List[int]:
    """按版本执行未应用的迁移，返回本次执行的版本号。每个迁移一个事务，执行前锁住迁移表。"""
    with database._db() as db:
        db.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL, "
                   "checksum TEXT NOT NULL, applied_at TIMESTAMPTZ NOT NULL)")
    known = {version for version, _, _ in migrations}
    with database._db() as db:
        newer = [row[0] for row in db.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
                 if row[0] not in known]
    if newer:
        raise RuntimeError(f"数据库已由更新版本的程序迁移（版本 {newer}），当前程序不能使用该数据库")
    applied: List[int] = []
    for version, name, migrate in migrations:
        checksum = _checksum(name)
        with database._db() as db:
            # 多个实例同时启动时，只有一个执行迁移，其余等待后跳过
            db.execute("LOCK TABLE schema_migrations IN EXCLUSIVE MODE")
            row = db.execute("SELECT name, checksum FROM schema_migrations WHERE version = %s", (version,)).fetchone()
            if row:
                if row["name"] != name or row["checksum"] != checksum:
                    raise RuntimeError(f"schema migration {version} checksum mismatch")
                continue
            migrate(db)
            db.execute("INSERT INTO schema_migrations(version, name, checksum, applied_at) VALUES (%s, %s, %s, %s)",
                       (version, name, checksum, utcnow()))
        logger.info("已执行数据库迁移 %s %s", version, name)
        applied.append(version)
    return applied


def schema_versions(database: Any) -> List[int]:
    with database._db() as db:
        return [row[0] for row in db.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()]
