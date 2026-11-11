"""PostgreSQL / GaussDB 数据层：并发领取、保存点、时间格式、兼容性检查与迁移版本保护。"""
import threading

import pytest

from swr_builder.store import IntegrityViolation, redact_url
from swr_builder.store.db import Database, constraint_name, is_unique_violation, normalize_url
from swr_builder.store.migrations import apply_migrations
from tests.dbutil import new_store


def _service(store, name="svc"):
    project = store.create_project(f"p-{name}", f"P {name}", [])["id"]
    cluster = store.create_cluster({"name": f"c-{name}", "auth_type": "sa_token", "server": "https://k.example",
                                    "credential_encrypted": "x"})
    namespace = store.create_namespace(cluster, "uat", "staging", {}, project)
    app = store.create_app({"name": f"app-{name}", "repository": name}, project)["id"]
    user = store.create_user(username=f"u-{name}", password_hash="x", role="member")["id"]
    service = store.create_service(name, project, namespace, app, {"mode": "image_only"}, user)
    return service, user


def _queue(store, service, user, key):
    return store.create_idempotent_service_deployment(
        target_name=service["name"], target_type="kubernetes", image="r/x@sha256:" + "a" * 64, project_id=service["project_id"],
        service_id=service["id"], namespace_id=service["namespace_id"], revision_id=service["current_revision_id"],
        kind="deploy", idempotency_key=key, request_hash=key, status="queued", created_by=user)


def test_concurrent_claims_never_run_one_service_twice():
    store = new_store()
    first, user = _service(store, "first")
    second, _ = _service(store, "second")
    queued = [_queue(store, first, user, f"k{i}") for i in range(3)] + [_queue(store, second, user, "k9")]
    barrier = threading.Barrier(6)
    claimed = []

    def worker(index):
        barrier.wait()
        row = store.claim_next_service_deployment(f"w{index}", 300)
        if row:
            claimed.append(row)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    by_service = {}
    for row in claimed:
        by_service.setdefault(row["service_id"], []).append(row)
    assert all(len(rows) == 1 for rows in by_service.values())  # 每个服务最多一个执行中的发布
    assert len({row["id"] for row in claimed}) == len(claimed)  # 同一任务不会被领两次
    active = [d for d in store.list_deployments(limit=100)[0] if d["status"] in ("applying", "verifying")]
    assert len(active) == len(by_service) and all(row["fencing_token"] == 1 for row in claimed)
    assert {d["id"] for d in queued} >= {row["id"] for row in claimed}


def test_idempotent_create_returns_existing_record_under_concurrency():
    store = new_store()
    service, user = _service(store)
    barrier = threading.Barrier(4)
    results = []

    def submit():
        barrier.wait()
        results.append(_queue(store, service, user, "same-key"))

    threads = [threading.Thread(target=submit) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len({row["id"] for row in results}) == 1 and store.list_deployments()[1] == 1


def test_insert_ignore_keeps_the_surrounding_transaction():
    store = new_store()
    project = store.default_project_id()
    with store._db() as db:
        assert store.insert_ignore(db, "project_registry_orgs", {"project_id": project, "namespace": "org"})
        assert not store.insert_ignore(db, "project_registry_orgs", {"project_id": project, "namespace": "org"})
        db.execute("UPDATE projects SET name = %s WHERE id = %s", ("改名后", project))  # 冲突之后事务仍可继续
    assert store.get_project(project)["name"] == "改名后"
    assert [o["namespace"] for o in store.get_project(project)["registry_orgs"]] == ["org"]


def test_timestamps_are_returned_as_utc_iso_strings():
    store = new_store()
    user = store.create_user(username="tz", password_hash="x", role="member")
    binding = store.create_binding("user", user["id"], "auditor", "global", 0, None, "2030-01-01T08:00:00+08:00")
    row = store.get_binding(binding)
    assert row["expires_at"] == "2030-01-01T00:00:00+00:00" and row["created_at"].endswith("+00:00")
    assert store.get_user(user["id"])["enabled"] is True  # 布尔列返回 bool


def test_foreign_key_errors_surface_as_integrity_violation():
    store = new_store()
    with pytest.raises(IntegrityViolation) as caught:
        store.create_app({"name": "orphan", "repository": "orphan"}, project_id=999999)
    assert caught.value.sqlstate == "23503"


def test_newer_schema_version_refuses_to_start():
    store = new_store()
    with store._db() as db:
        db.execute("INSERT INTO schema_migrations(version, name, checksum, applied_at) VALUES (999, 'future', 'x', now())")
    with pytest.raises(RuntimeError, match="更新版本"):
        apply_migrations(store)


def test_url_handling_hides_passwords_and_accepts_gaussdb_scheme():
    assert redact_url("postgresql://swr:secret@db:5432/swr") == "postgresql://swr:***@db:5432/swr"
    assert normalize_url("gaussdb://u@h:8000/swr") == "postgresql://u@h:8000/swr"
    with pytest.raises(ValueError):
        normalize_url("sqlite:///data/builder.db")


def test_constraint_name_falls_back_to_error_message():
    """openGauss 的错误响应没有约束名字段，只能从消息中取。"""
    class Diag:
        constraint_name = None
        message_primary = 'duplicate key value violates unique constraint "uq_role_bindings_grant"'

    class UniqueError(Exception):
        sqlstate = "23505"
        diag = Diag()

    error = UniqueError("duplicate key")
    assert constraint_name(error) == "uq_role_bindings_grant"
    assert is_unique_violation(error, "uq_role_bindings_grant") and not is_unique_violation(error, "other")


class _FakeCursor:
    def __init__(self, value):
        self.value = value

    def fetchone(self):
        return (self.value,)


class _FakeGauss:
    """模拟 GaussDB：version() 带 openGauss 标识，兼容模式可配置。"""

    def __init__(self, compatibility):
        self.compatibility = compatibility

    def execute(self, sql, params=()):
        if "version()" in sql:
            return _FakeCursor("PostgreSQL 9.2.4 (openGauss 5.0.3 build 89d339ca) compiled at 2024-07-29")
        if "datcompatibility" in sql:
            return _FakeCursor(self.compatibility)
        if "server_encoding" in sql:
            return _FakeCursor("UTF8")
        raise AssertionError(sql)


def test_gaussdb_requires_pg_compatibility_mode():
    database = Database.__new__(Database)
    with pytest.raises(RuntimeError, match="PG 兼容模式"):
        database._inspect_server(_FakeGauss("A"))
    database._inspect_server(_FakeGauss("PG"))
    assert database.flavor == "gaussdb"
