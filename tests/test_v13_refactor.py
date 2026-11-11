"""v1.3 重构新增行为：前端托管、错误码、设置回滚、构建排队顺序、发布 worker 容错与租约。"""
import asyncio

import pytest
from fastapi.testclient import TestClient

from swr_builder.builder import BuildManager
from swr_builder.config import Settings
from swr_builder.deploy import worker as worker_module
from swr_builder.events import EventBus
from swr_builder.images import dockerfile_from_images, registry_path_parts
from swr_builder.main import create_app
from swr_builder.services.namespaces import effective_policy, required_approvals
from swr_builder.store import utcnow
from swr_builder.web import spa
from tests.dbutil import new_store
from tests.test_accounts_settings import HEAD, login


@pytest.fixture()
def client(tmp_path):
    s = Settings()
    s.build.data_dir = str(tmp_path / "data")
    s.server.admin_password = "AdminPass123"
    app = create_app(s)
    with TestClient(app, headers=HEAD) as c:
        yield s, app, c


# ------------------------------------------------------------------ 前端托管与安全头
def test_spa_routes_auth_and_headers(client, tmp_path, monkeypatch):
    webui = tmp_path / "webui"
    (webui / "assets").mkdir(parents=True)
    (webui / "index.html").write_text("<!doctype html><div id=app></div>", encoding="utf-8")
    (webui / "assets" / "index-abc123.js").write_text("console.log(1)", encoding="utf-8")
    monkeypatch.setattr(spa, "WEBUI_DIR", webui)
    _, _, c = client
    # 登录页公开；其他页面未登录时 303 到登录页并带上原地址
    page = c.get("/login")
    assert page.status_code == 200 and 'id=app' in page.text
    assert "default-src 'self'" in page.headers["content-security-policy"]
    redirect = c.get("/jobs/12?tab=log", follow_redirects=False)
    assert redirect.status_code == 303
    assert redirect.headers["location"] == "/login?next=%2Fjobs%2F12%3Ftab%3Dlog"
    login(c)
    deep = c.get("/resources/services/3")
    assert deep.status_code == 200 and 'id=app' in deep.text and deep.headers["cache-control"] == "no-cache"
    # 未注册的 API 不会落到前端页面
    unknown = c.get("/api/new-feature")
    assert unknown.status_code == 403 and unknown.json()["code"] == "ROUTE_NOT_REGISTERED"
    # 哈希静态资源公开且长期缓存
    c.cookies.clear()
    asset = c.get("/assets/index-abc123.js")
    assert asset.status_code == 200 and "immutable" in asset.headers["cache-control"]
    assert c.get("/assets/missing.js").status_code == 404


def test_placeholder_page_when_frontend_not_built(client, tmp_path, monkeypatch):
    monkeypatch.setattr(spa, "WEBUI_DIR", tmp_path / "missing")
    _, _, c = client
    page = c.get("/login")
    assert page.status_code == 200 and "前端页面尚未构建" in page.text


def test_api_docs_disabled_by_default(client):
    _, _, c = client
    login(c)
    # 关闭后这些路径落到前端页面，不再返回 Swagger UI 或 OpenAPI 文档
    assert "swagger" not in c.get("/docs").text.lower()
    assert c.get("/openapi.json").headers["content-type"].startswith("text/html")


def test_errors_carry_machine_readable_code(client):
    _, _, c = client
    assert c.get("/api/apps").json() == {"detail": "请先登录", "code": "UNAUTHENTICATED"}
    bad = c.post("/api/auth/login", json={"username": "admin", "password": "wrong-password"})
    assert bad.status_code == 401 and bad.json()["code"] == "AUTH_FAILED"
    login(c)
    missing = c.put("/api/apps/999", json={"name": "x", "repository": "x"})
    assert missing.status_code == 404 and missing.json() == {"detail": "应用不存在", "code": "NOT_FOUND"}
    invalid = c.post("/api/projects", json={"slug": "Bad Slug", "name": "x"})
    assert invalid.status_code == 400 and invalid.json()["code"] == "BAD_REQUEST"


def test_business_audit_is_not_duplicated(client):
    _, app, c = client
    login(c)
    assert c.post("/api/projects", json={"slug": "audit-once", "name": "审计"}).status_code == 201
    actions = [row["action"] for row in app.state.store.list_audit(limit=100)[0]]
    assert actions.count("project.create") == 1
    assert "POST /api/projects" not in actions


# ------------------------------------------------------------------ 设置回滚（B1）
def test_settings_rollback_restores_memory_and_database(client):
    settings, app, c = client
    login(c)
    calls = []

    def flaky(paths):
        calls.append(paths)
        raise RuntimeError("callback boom")  # 第一次（应用）和第二次（回滚）都失败

    app.state.ctx.sysconfig.on_change.append(flaky)
    before = settings.general.site_name
    r = c.put("/api/settings", json={"values": {"general.site_name": "新名字"}})
    assert r.status_code == 400 and "设置应用失败" in r.json()["detail"]
    assert settings.general.site_name == before
    assert "general.site_name" not in app.state.store.get_settings()
    assert len(calls) == 2


# ------------------------------------------------------------------ 构建排队（B2/B3）
def test_same_repository_queue_does_not_hold_concurrency_slot(tmp_path):
    settings = Settings()
    settings.build.data_dir = str(tmp_path / "data")
    settings.build.max_concurrent = 2
    settings.registry.organization = "org"
    store = new_store()
    manager = BuildManager(settings, store, registry=None, events=EventBus())
    running, release = [], asyncio.Event()

    async def fake_execute(job_id, job, app, log):
        running.append(job_id)
        await release.wait()
        store.update_job(job_id, status="success", finished_at=utcnow())

    manager._execute = fake_execute

    def job(repo):
        app = {"name": repo, "repository": repo}
        return store.create_job(app_id=1, app_name=repo, repository=repo, requested_mode="full", mode="full",
                                status="queued", step="queued", app_snapshot=app, created_at=utcnow())

    async def scenario():
        await manager.start()
        a1, a2, b1 = job("a"), job("a"), job("b")
        for j in (a1, a2, b1):
            manager.submit(j)
        for _ in range(100):
            if len(running) >= 2:
                break
            await asyncio.sleep(0.01)
        assert running == [a1, b1]  # a2 在仓库 a 的锁上等待，不占并发名额
        release.set()
        for _ in range(200):
            if not manager.has_running_jobs():
                break
            await asyncio.sleep(0.01)
        assert running == [a1, b1, a2]
        assert manager._repo_locks == {}  # 锁按引用计数回收

    asyncio.run(scenario())


# ------------------------------------------------------------------ 发布 worker（B4/B5/B6/B7）
def test_worker_loop_survives_store_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(worker_module, "ERROR_BACKOFF_SECONDS", 0.01)
    store = new_store()
    calls = {"n": 0}
    original = store.claim_next_service_deployment

    def flaky_claim(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("database temporarily unavailable")
        return original(*args, **kwargs)

    store.claim_next_service_deployment = flaky_claim
    settings = Settings()
    settings.build.data_dir = str(tmp_path / "data")
    worker = worker_module.ServiceDeploymentWorker(settings, store, box=None, events=EventBus())

    async def scenario():
        worker.start()
        for _ in range(100):
            if calls["n"] >= 2:
                break
            await asyncio.sleep(0.01)
        assert calls["n"] >= 2 and not worker._task.done()
        await worker.stop()

    asyncio.run(scenario())


def _active_deployment(store, lease_until, status="applying"):
    return store.create_deployment(target_name="svc", target_type="kubernetes", app_id=1, app_name="a", job_id=1,
                                   image="r/x@sha256:" + "a" * 64, service_id=1, namespace_id=1, revision_id=1,
                                   status=status, lease_owner="w1", lease_until=lease_until, fencing_token=3)


def test_lease_renewal_and_stale_lease_expiry(tmp_path):
    store = new_store()
    live = _active_deployment(store, "2999-01-01T00:00:00+00:00")
    assert store.renew_service_deployment_lease(live, "w1", 3, 300)
    assert not store.renew_service_deployment_lease(live, "w1", 2, 300)  # 旧 fencing token
    assert not store.renew_service_deployment_lease(live, "w2", 3, 300)  # 其他 worker
    assert store.service_deployment_lease_valid(live, "w1", 3)
    store.update_deployment(live, status="success", lease_owner=None, lease_until=None)

    stale = _active_deployment(store, "2000-01-01T00:00:00+00:00", status="verifying")
    assert not store.renew_service_deployment_lease(stale, "w1", 3, 300)  # 已过期不能续
    assert store.expire_stale_service_leases("lease expired") == 1
    row = store.get_deployment(stale)
    assert row["status"] == "unknown" and row["lease_owner"] is None and row["error"] == "lease expired"


def test_policy_defaults_for_legacy_namespace_data():
    legacy = {"min_approvals": 2, "allowed_registry_orgs": ["team"], "freeze": {"enabled": False}}
    policy = effective_policy(legacy)
    assert policy["rollout_timeout"] == 600 and policy["require_approval"] is True
    assert policy["freeze"] == {"enabled": False, "until": None, "reason": ""}
    assert required_approvals(policy, "dev") == 2
    assert required_approvals({"require_approval": False}, "dev") == 0
    assert required_approvals({"require_approval": False}, "prod") == 1


# ------------------------------------------------------------------ 共用解析函数
def test_image_helpers():
    assert dockerfile_from_images(["FROM --platform=linux/arm64 a/b:1 AS x\nRUN x\nfrom c", "FROM"]) == ["a/b:1", "c", None]
    assert registry_path_parts("swr.example/team/api/base:1") == ("team", "api/base")
    assert registry_path_parts("plain:1") is None
