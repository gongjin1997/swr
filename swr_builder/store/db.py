"""PostgreSQL / GaussDB 连接、连接池、事务与通用写入。

SQL 只使用 PostgreSQL 14+ 与 GaussDB / openGauss（PG 兼容模式）都支持的部分：
- 自增主键用 BIGSERIAL，不用 IDENTITY 列；
- 不用 ON CONFLICT、ON DUPLICATE KEY、SKIP LOCKED、MERGE、聚合 FILTER 子句；
  "插入或忽略""插入或更新"用保存点加唯一冲突处理（`insert_ignore`、`upsert`）；
- JSON 存 TEXT，由 Python 编解码；时间用 TIMESTAMPTZ，读出后统一转成 UTC ISO 字符串，与 v1.x 的返回格式一致；
- 占位符为 %s，SQL 字面量里的 % 写成 %%。

数据层只向上抛两类错误：唯一约束冲突转换为 errors.Conflict（409）；其余约束错误为 IntegrityViolation，
由全局处理器转换为 409；参数或程序错误保持原样。
"""
from __future__ import annotations

import importlib
import logging
import threading
import time
import weakref
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple
from urllib.parse import urlsplit, urlunsplit

from ..errors import Conflict

logger = logging.getLogger("swr_builder.store")

DRIVERS = {"psycopg": "psycopg", "gaussdb": "gaussdb"}
UNIQUE_VIOLATION = "23505"
BOOLEAN_COLUMNS = frozenset({"enabled", "must_change_password"})
_OPEN: "weakref.WeakSet[Database]" = weakref.WeakSet()


class IntegrityViolation(Exception):
    """外键、非空、检查约束等数据约束错误（唯一冲突另见 Conflict）。"""

    def __init__(self, message: str, sqlstate: str = "", constraint: str = ""):
        super().__init__(message)
        self.sqlstate = sqlstate
        self.constraint = constraint


def sqlstate(exc: BaseException) -> str:
    return getattr(exc, "sqlstate", None) or ""


def constraint_name(exc: BaseException) -> str:
    return getattr(getattr(exc, "diag", None), "constraint_name", None) or ""


def is_unique_violation(exc: BaseException, constraint: Optional[str] = None) -> bool:
    return sqlstate(exc) == UNIQUE_VIOLATION and (constraint is None or constraint_name(exc) == constraint)


def redact_url(url: str) -> str:
    """去掉连接串中的口令，用于日志和错误信息。"""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<invalid database url>"
    if parts.password is None:
        return url
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    user = f"{parts.username}:***@" if parts.username else ""
    return urlunsplit((parts.scheme, user + host, parts.path, parts.query, parts.fragment))


def normalize_url(url: str) -> str:
    """接受 postgresql://、postgres://、gaussdb://、opengauss://，统一为 libpq 能识别的 postgresql://。"""
    url = (url or "").strip()
    scheme, sep, rest = url.partition("://")
    if not sep or scheme.lower() not in ("postgresql", "postgres", "gaussdb", "opengauss"):
        raise ValueError("数据库连接串必须以 postgresql:// 开头（GaussDB 也使用 postgresql:// 或 gaussdb://）")
    return "postgresql://" + rest


def load_driver(name: str) -> Any:
    module = DRIVERS.get(name)
    if not module:
        raise ValueError(f"不支持的数据库驱动 {name}，可选 {', '.join(DRIVERS)}")
    try:
        return importlib.import_module(module)
    except ImportError as exc:
        hint = "pip install 'psycopg[binary]'" if name == "psycopg" else "pip install gaussdb，并安装 GaussDB 客户端 libpq"
        raise RuntimeError(f"数据库驱动 {name} 不可用（{exc}）；请 {hint}") from None


def _to_text(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat(timespec="seconds")
    return value


class Row(dict):
    """既能按列名取值，也能按位置取值（兼容 `fetchone()[0]`）。"""

    __slots__ = ("_values",)

    def __init__(self, names: Sequence[str], values: Sequence[Any]):
        super().__init__(zip(names, values))
        self._values = values

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, int):
            return self._values[key]
        return dict.__getitem__(self, key)


def row_factory(cursor: Any) -> Callable[[Sequence[Any]], Any]:
    description = cursor.description
    if not description:
        return tuple
    names = [column.name for column in description]

    def make(values: Sequence[Any]) -> Row:
        return Row(names, tuple(_to_text(v) for v in values))

    return make


class _Pool:
    """最小连接池：同一时刻一个连接只给一个线程用；空闲较久的连接复用前先检查。"""

    def __init__(self, connect: Callable[[], Any], max_size: int, recheck_after: float = 30.0):
        self._connect = connect
        self._idle: List[Tuple[Any, float]] = []
        self._lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(max(1, max_size))
        self._recheck_after = recheck_after
        self._closed = False

    @staticmethod
    def _discard(conn: Any) -> None:
        try:
            conn.close()
        except Exception:  # noqa: BLE001 - 关闭失败不影响调用方
            pass

    def acquire(self, timeout: float) -> Any:
        if self._closed:
            raise RuntimeError("数据库连接池已关闭")
        if not self._slots.acquire(timeout=timeout):
            raise TimeoutError(f"等待数据库连接超时（{timeout:.0f} 秒）")
        try:
            while True:
                with self._lock:
                    item = self._idle.pop() if self._idle else None
                if item is None:
                    return self._connect()
                conn, last_used = item
                if conn.closed or conn.broken:
                    self._discard(conn)
                    continue
                if time.monotonic() - last_used > self._recheck_after:
                    try:
                        conn.execute("SELECT 1")
                        conn.rollback()
                    except Exception:  # noqa: BLE001 - 服务端已断开，换一个连接
                        self._discard(conn)
                        continue
                return conn
        except BaseException:
            self._slots.release()
            raise

    def release(self, conn: Any, discard: bool = False) -> None:
        try:
            if discard or self._closed or conn.closed or conn.broken:
                self._discard(conn)
                return
            if conn.info.transaction_status.name != "IDLE":
                conn.rollback()
            with self._lock:
                self._idle.append((conn, time.monotonic()))
        except Exception:  # noqa: BLE001 - 状态异常的连接直接丢弃
            self._discard(conn)
        finally:
            self._slots.release()

    def close(self) -> None:
        self._closed = True
        with self._lock:
            idle, self._idle = self._idle, []
        for conn, _ in idle:
            self._discard(conn)


class Database:
    """连接池与事务。每个 Store 方法通过一次 `_db()` 完成一个事务；同一线程内不能嵌套调用。"""

    def __init__(self, url: str, *, driver: str = "psycopg", password: str = "", pool_size: int = 10,
                 connect_timeout: int = 10, pool_timeout: float = 30.0, migrate: bool = True):
        from .migrations import apply_migrations

        self.url = redact_url(url)
        self.driver_name = driver
        self._driver = load_driver(driver)
        self._conninfo = normalize_url(url)
        self._connect_kwargs: Dict[str, Any] = {"connect_timeout": int(connect_timeout),
                                                "application_name": "swr-builder"}
        if password:
            self._connect_kwargs["password"] = password
        self._pool_timeout = pool_timeout
        self._pool = _Pool(self._connect, pool_size)
        self._local = threading.local()
        self.flavor = "postgresql"
        self.server_version = ""
        _OPEN.add(self)
        with self._db() as db:
            self._inspect_server(db)
        if migrate:
            apply_migrations(self)

    # ------------------------------------------------------------------ 连接
    def _connect(self) -> Any:
        try:
            conn = self._driver.connect(self._conninfo, autocommit=False, prepare_threshold=None,
                                        row_factory=row_factory, **self._connect_kwargs)
        except Exception as exc:
            raise RuntimeError(f"无法连接数据库 {self.url}：{str(exc).strip()}") from None
        try:
            conn.execute("SET TIME ZONE 'UTC'")
            conn.commit()
        except Exception:
            _Pool._discard(conn)
            raise
        return conn

    def _inspect_server(self, db: Any) -> None:
        version = db.execute("SELECT version()").fetchone()[0] or ""
        self.server_version = version
        lowered = version.lower()
        if "opengauss" in lowered or "gaussdb" in lowered:
            self.flavor = "gaussdb"
            row = db.execute("SELECT datcompatibility FROM pg_database WHERE datname = current_database()").fetchone()
            mode = (row[0] if row else "") or ""
            if mode.upper() != "PG":
                raise RuntimeError(f"GaussDB 数据库的兼容模式为 {mode or '未知'}，平台要求 PG 兼容模式："
                                   "请用 CREATE DATABASE <名称> DBCOMPATIBILITY 'PG' 创建数据库后再连接")
        else:
            number = int(db.execute("SELECT current_setting('server_version_num')").fetchone()[0])
            if number < 140000:
                logger.warning("PostgreSQL 版本 %s 低于平台测试过的 14，部分功能可能不可用", number)
        encoding = db.execute("SELECT current_setting('server_encoding')").fetchone()[0]
        if str(encoding).upper() not in ("UTF8", "UTF-8"):
            raise RuntimeError(f"数据库编码为 {encoding}，平台要求 UTF8")

    def close(self) -> None:
        self._pool.close()
        _OPEN.discard(self)

    # ------------------------------------------------------------------ 事务
    @contextmanager
    def _db(self) -> Iterator[Any]:
        if getattr(self._local, "active", False):
            raise RuntimeError("Store._db() 不能嵌套调用；请在同一个事务内完成查询")
        conn = self._pool.acquire(self._pool_timeout)
        self._local.active = True
        discard = False
        try:
            try:
                yield conn
                conn.commit()
            except BaseException:
                try:
                    conn.rollback()
                except Exception:  # noqa: BLE001 - 连接已不可用
                    discard = True
                raise
        except Exception as exc:
            state = sqlstate(exc)
            if state.startswith("23") and state != UNIQUE_VIOLATION:
                raise IntegrityViolation(str(exc).strip().splitlines()[0], state, constraint_name(exc)) from None
            raise
        finally:
            self._local.active = False
            self._pool.release(conn, discard=discard)

    @staticmethod
    @contextmanager
    def _unique(message: str, constraint: Optional[str] = None) -> Iterator[None]:
        """把唯一约束冲突转换为 Conflict(message)；constraint 为空时匹配任意唯一约束。"""
        try:
            yield
        except Exception as exc:
            if is_unique_violation(exc, constraint):
                raise Conflict(message) from None
            raise

    @staticmethod
    def attempt(db: Any, sql: str, params: Sequence[Any] = ()) -> Optional[Any]:
        """在保存点内执行一条语句；唯一约束冲突时回滚到保存点并返回 None，事务可以继续。"""
        db.execute("SAVEPOINT swr_attempt")
        try:
            cursor = db.execute(sql, params)
        except Exception as exc:
            db.execute("ROLLBACK TO SAVEPOINT swr_attempt")
            if is_unique_violation(exc):
                return None
            raise
        db.execute("RELEASE SAVEPOINT swr_attempt")
        return cursor

    @classmethod
    def insert_ignore(cls, db: Any, table: str, values: Dict[str, Any]) -> bool:
        """插入一行；违反唯一约束时忽略。返回是否插入。"""
        cols = ", ".join(values)
        marks = ", ".join("%s" for _ in values)
        return cls.attempt(db, f"INSERT INTO {table}({cols}) VALUES ({marks})", tuple(values.values())) is not None

    @classmethod
    def upsert(cls, db: Any, table: str, key: Dict[str, Any], values: Dict[str, Any]) -> None:
        """按 key 更新 values；不存在时插入，并发插入冲突时再更新一次。"""
        sets = ", ".join(f"{column} = %s" for column in values)
        where = " AND ".join(f"{column} = %s" for column in key)
        update = f"UPDATE {table} SET {sets} WHERE {where}"
        params = (*values.values(), *key.values())
        if db.execute(update, params).rowcount:
            return
        if not cls.insert_ignore(db, table, {**key, **values}):
            db.execute(update, params)

    # ------------------------------------------------------------------ 通用写入
    @staticmethod
    def coerce(fields: Dict[str, Any]) -> Dict[str, Any]:
        """布尔列只接受布尔值；v1.x 的调用方会传 0/1。"""
        return {k: (bool(v) if k in BOOLEAN_COLUMNS and v is not None else v) for k, v in fields.items()}

    def _insert(self, table: str, allowed: Tuple[str, ...], fields: Dict[str, Any]) -> int:
        unknown = set(fields) - set(allowed)
        if unknown:
            raise KeyError(", ".join(sorted(unknown)))
        fields = self.coerce(fields)
        cols = ", ".join(fields)
        marks = ", ".join("%s" for _ in fields)
        with self._db() as db:
            return int(db.execute(f"INSERT INTO {table}({cols}) VALUES ({marks}) RETURNING id",
                                  tuple(fields.values())).fetchone()["id"])

    def _update(self, table: str, allowed: Tuple[str, ...], row_id: int, fields: Dict[str, Any]) -> bool:
        if not fields:
            return True
        unknown = set(fields) - set(allowed)
        if unknown:
            raise KeyError(", ".join(sorted(unknown)))
        fields = self.coerce(fields)
        sets = ", ".join(f"{k} = %s" for k in fields)
        with self._db() as db:
            return db.execute(f"UPDATE {table} SET {sets} WHERE id = %s", (*fields.values(), row_id)).rowcount > 0

    @staticmethod
    def marks(values: Iterable[Any]) -> str:
        """IN 子句占位符：`IN ({marks(ids)})`。"""
        return ", ".join("%s" for _ in values)


def close_all() -> None:
    """关闭本进程打开的全部连接池（测试结束时使用）。"""
    for database in list(_OPEN):
        database.close()
