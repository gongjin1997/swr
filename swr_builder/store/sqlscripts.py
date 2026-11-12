"""执行离线补丁包与铺底数据中的 SQL 脚本，并记录在 patch_scripts 表中。

目录约定（补丁包的 sql/ 目录、完整包的 seed/ 目录）：
- 可以分 ddl/、dml/ 两个子目录，也可以直接放 *.sql；执行顺序为 ddl/ → dml/ → 目录下的 *.sql，
  同一目录内按文件名排序（建议 001_add_index.sql 这样带序号命名）；
- README.md 等说明文件忽略；其他文件或子目录报错，避免漏执行。

执行规则：
- 每个脚本在一个事务中执行，脚本与它的执行记录一起提交；任一语句失败，整个脚本回滚，后面的脚本不再执行；
- 第一行写 `-- swr:no-transaction` 的脚本不包在事务里（例如 CREATE INDEX CONCURRENTLY），失败时可能已部分生效；
- 同一补丁（patch）同一路径的脚本只执行一次：再次执行时内容相同则跳过，内容不同则报错（已发布的脚本不能修改）；
- 脚本必须是 UTF-8，不能自己写 BEGIN; / COMMIT;，不支持 psql 元命令（\\c、\\i 等以反斜杠开头的行）。

平台自己的表结构变化写成迁移（store/migrations.py），随代码发布；这里的脚本用于迁移之外的调整
（例如索引、数据修正、现场铺底数据）。

本模块顶层只依赖标准库：打包工具（deploy/offline/release.py）按文件路径加载它，在制作补丁包时提前校验脚本。
"""
from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.sql$")
PATCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
NO_TRANSACTION = "-- swr:no-transaction"
MAX_SCRIPT_BYTES = 20 * 1024 * 1024
SUBDIRECTORIES = ("ddl", "dml")
IGNORED = re.compile(r"(?i)^(readme(\.(md|txt))?|.*\.md|\.gitkeep)$")
# 独占一行的 BEGIN; / COMMIT; 等事务控制语句（PL/pgSQL 的 BEGIN ... END; 块不受影响）
_TRANSACTION_CONTROL = re.compile(
    r"(?im)^\s*(BEGIN|START\s+TRANSACTION|COMMIT|ROLLBACK)\s*(WORK|TRANSACTION)?\s*;\s*$")
_META_COMMAND = re.compile(r"(?m)^\s*\\")


@dataclass(frozen=True)
class Script:
    path: str            # 相对路径，例如 ddl/001_add_index.sql
    sha256: str
    text: str
    transactional: bool


def _strip_comments(text: str) -> str:
    """去掉 -- 行注释与 /* */ 块注释，只用于检查（执行的仍是原文）。不处理字符串里的注释符号。"""
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    return re.sub(r"--[^\n]*", "", text)


def read_script(file: Path, relative: str) -> Script:
    data = file.read_bytes()
    if len(data) > MAX_SCRIPT_BYTES:
        raise ValueError(f"{relative} 超过 {MAX_SCRIPT_BYTES // 1024 // 1024} MiB")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError(f"{relative} 不是 UTF-8 编码") from None
    if not _strip_comments(text).strip():
        raise ValueError(f"{relative} 没有可执行的语句")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    transactional = lines[0].lower() != NO_TRANSACTION
    if _META_COMMAND.search(text):
        raise ValueError(f"{relative} 包含 psql 元命令（以反斜杠开头的行），平台用数据库驱动执行脚本，不支持元命令")
    if transactional and _TRANSACTION_CONTROL.search(_strip_comments(text)):
        raise ValueError(f"{relative} 自己写了 BEGIN / COMMIT：平台会把每个脚本包在一个事务里执行；"
                         f"确实需要在事务外执行时，第一行写 {NO_TRANSACTION}")
    return Script(relative, hashlib.sha256(data).hexdigest(), text, transactional)


def discover(directory: Path) -> List[Script]:
    """按执行顺序列出目录中的脚本；目录不存在或为空时返回空列表。"""
    if not directory.is_dir():
        return []
    for item in sorted(directory.iterdir()):
        if item.is_dir() and item.name not in SUBDIRECTORIES:
            raise ValueError(f"SQL 目录中不认识的子目录 {item.name}/：只支持 ddl/ 与 dml/")
    scripts: List[Script] = []
    for sub in (*SUBDIRECTORIES, ""):
        folder = directory / sub if sub else directory
        if not folder.is_dir():
            continue
        for file in sorted(folder.iterdir(), key=lambda p: p.name):
            if file.is_dir():
                if sub:
                    raise ValueError(f"{sub}/{file.name}/：ddl/ 与 dml/ 下不能再有子目录")
                continue
            relative = f"{sub}/{file.name}" if sub else file.name
            if file.is_symlink():
                raise ValueError(f"{relative} 是符号链接")
            if IGNORED.fullmatch(file.name):
                continue
            if not NAME_RE.fullmatch(file.name):
                raise ValueError(f"{relative} 不是 .sql 文件，或文件名含有字母、数字、. _ - 以外的字符")
            scripts.append(read_script(file, relative))
    return scripts


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _check_patch_name(patch: str) -> None:
    if not PATCH_RE.fullmatch(patch or ""):
        raise ValueError("补丁名只能包含字母、数字、点、下划线和短横线，最长 80 字符")


def _has_history_table(store: Any) -> bool:
    with store._db() as db:
        return bool(db.execute("SELECT COUNT(*) FROM information_schema.tables "
                               "WHERE table_schema = current_schema() AND table_name = 'patch_scripts'").fetchone()[0])


def applied_scripts(store: Any, patch: str = "") -> List[Dict[str, Any]]:
    """已执行的脚本（按执行顺序）；patch 为空时返回全部。"""
    with store._db() as db:
        if patch:
            rows = db.execute("SELECT patch, path, sha256, transactional, duration_ms, applied_at FROM patch_scripts "
                              "WHERE patch = %s ORDER BY id", (patch,)).fetchall()
        else:
            rows = db.execute("SELECT patch, path, sha256, transactional, duration_ms, applied_at FROM patch_scripts "
                              "ORDER BY id").fetchall()
    return [dict(row) for row in rows]


def _changed(patch: str, path: str) -> RuntimeError:
    return RuntimeError(f"{path} 已在 {patch} 中执行过，但内容与本次不同；已发布的脚本不能修改，请新建一个脚本")


def plan_scripts(store: Any, patch: str, scripts: List[Script]) -> List[Dict[str, Any]]:
    """只核对、不执行：每个脚本是将要执行（pending）还是已执行过（skipped）；已执行过但内容不同时报错。

    离线控制器在停机前用它预检补丁；数据库还没有执行迁移 4（没有 patch_scripts 表）时视为没有执行记录。
    """
    _check_patch_name(patch)
    done = ({row["path"]: row["sha256"] for row in applied_scripts(store, patch)}
            if _has_history_table(store) else {})
    plan: List[Dict[str, Any]] = []
    for script in scripts:
        recorded = done.get(script.path)
        if recorded and recorded != script.sha256:
            raise _changed(patch, script.path)
        plan.append({"path": script.path, "status": "skipped" if recorded else "pending",
                     "transactional": script.transactional})
    return plan


def apply_scripts(store: Any, patch: str, scripts: List[Script],
                  log: Callable[[str], None] = print) -> List[Dict[str, Any]]:
    """依次执行脚本并记录；返回每个脚本的结果（applied / skipped）。失败时抛出 RuntimeError，说明是哪个脚本。"""
    _check_patch_name(patch)
    done = {row["path"]: row["sha256"] for row in applied_scripts(store, patch)}
    results: List[Dict[str, Any]] = []
    for script in scripts:
        recorded = done.get(script.path)
        if recorded == script.sha256:
            log(f"跳过 {script.path}：已执行过")
            results.append({"path": script.path, "status": "skipped"})
            continue
        if recorded:
            raise _changed(patch, script.path)
        log(f"执行 {script.path}" + ("" if script.transactional else "（不在事务中）"))
        started = time.monotonic()
        try:
            if script.transactional:
                with store._db() as db:
                    db.execute(script.text)
                    _record(db, patch, script, started)
            else:
                store.execute_autocommit(script.text)
                with store._db() as db:
                    _record(db, patch, script, started)
        except Exception as exc:
            message = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
            hint = "已回滚该脚本" if script.transactional else "该脚本不在事务中执行，可能已部分生效，请人工核对"
            raise RuntimeError(f"执行 {script.path} 失败（{hint}）：{message}") from None
        results.append({"path": script.path, "status": "applied",
                        "duration_ms": int((time.monotonic() - started) * 1000)})
    return results


def _record(db: Any, patch: str, script: Script, started: float) -> None:
    db.execute("INSERT INTO patch_scripts(patch, path, sha256, transactional, duration_ms, applied_at) "
               "VALUES (%s, %s, %s, %s, %s, %s)",
               (patch, script.path, script.sha256, script.transactional, int((time.monotonic() - started) * 1000),
                _utcnow()))


ACTIVE_JOB_STATUSES = ("queued", "running")
ACTIVE_DEPLOYMENT_STATUSES = ("queued", "running", "applying", "verifying")


def status(store: Any) -> Dict[str, Any]:
    """数据库状态，离线控制器据此判断能否停机、切换代码：

    - migrations：数据库已执行的迁移；pending：本程序有、数据库还没执行的迁移（升级时会执行）；
      unknown：数据库有、本程序不认识的迁移（本程序版本比数据库旧，不能用它启动）；
    - active：未结束的构建与发布数量；scripts：执行过的补丁脚本数量；users：账号数量。
    """
    from .. import __version__
    from .migrations import MIGRATIONS

    with store._db() as db:
        tables = {row[0] for row in db.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema()").fetchall()}
        migrations = ([row[0] for row in db.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()]
                      if "schema_migrations" in tables else [])
        active = {"jobs": 0, "deployments": 0}
        for table, statuses in (("jobs", ACTIVE_JOB_STATUSES), ("deployments", ACTIVE_DEPLOYMENT_STATUSES)):
            if table in tables:
                marks = ", ".join(["%s"] * len(statuses))
                active[table] = db.execute(f"SELECT COUNT(*) FROM {table} WHERE status IN ({marks})",
                                           statuses).fetchone()[0]
        scripts = db.execute("SELECT COUNT(*) FROM patch_scripts").fetchone()[0] if "patch_scripts" in tables else 0
        users = db.execute("SELECT COUNT(*) FROM users").fetchone()[0] if "users" in tables else 0
    known = [version for version, _, _ in MIGRATIONS]
    return {"version": __version__, "migrations": migrations,
            "pending": [v for v in known if v not in migrations],
            "unknown": [v for v in migrations if v not in known],
            "active": active, "scripts": scripts, "users": users, "flavor": store.flavor}
