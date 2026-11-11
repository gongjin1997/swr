"""端到端测试：模拟 SWR + 假 docker，走完 上传 → 取上一版本 → 累加构建 → 推送 全流程。"""
import json
import sys
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from swr_builder.config import Settings  # noqa: E402
from swr_builder.main import create_app  # noqa: E402
from tests.mock_registry import PASSWORD, USER, add_image, create_mock_registry, load_state  # noqa: E402

FAKE_DOCKER = Path(__file__).resolve().parent / "fake_docker.py"
REPO = "metaerp-beta15/cnnc-coding-chc-fin"
DOCKERFILE = (
    "FROM swr.mock/metaerp_sdk/java:8-huawei-r30-arm64\r\n"
    "# FROM swr.mock/cnnc-erp-pro/java:8-huawei-r30-arm64\r\n"
    "MAINTAINER FAN\r\n"
    "WORKDIR /app\r\n"
    "COPY --chown=nonroot:nonroot  cnnc-coding-fin-1.0.0-SNAPSHOT.jar cnnc-coding-chc-fin.jar\r\n"
    "EXPOSE 8080\r\n"
    'CMD ["-jar","/app/cnnc-coding-chc-fin.jar"]\r\n'
)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    registry_state = tmp_path / "registry.json"
    local_state = tmp_path / "local.json"
    docker_log = tmp_path / "docker.log"
    monkeypatch.setenv("MOCK_REGISTRY_STATE", str(registry_state))
    monkeypatch.setenv("FAKE_DOCKER_STATE", str(local_state))
    monkeypatch.setenv("FAKE_DOCKER_LOG", str(docker_log))
    monkeypatch.setenv("FAKE_DOCKER_PASSWORD", PASSWORD)
    monkeypatch.setenv("FAKE_DOCKER_ARCH", "amd64")  # 模拟 x86 构建机
    add_image(registry_state, "metaerp_sdk/java", "8-huawei-r30-arm64", layers=5)
    for tag in ("1.0.5", "1.0.7", "1.0.6", "latest"):
        add_image(registry_state, REPO, tag, layers=8)

    settings = Settings()
    settings.server.admin_password = "TestAdmin123"
    settings.registry.address = "swr.mock"
    settings.registry.organization = "metaerp-beta15"
    settings.registry.username = USER
    settings.registry.password = PASSWORD
    settings.build.docker_bin = str(FAKE_DOCKER)
    settings.build.data_dir = str(tmp_path / "data")
    transport = httpx.ASGITransport(app=create_mock_registry(registry_state))
    with TestClient(create_app(settings, registry_transport=transport)) as client:
        client.headers["X-Requested-With"] = "XMLHttpRequest"
        assert client.post("/api/auth/login", json={"username":"admin","password":"TestAdmin123"}).status_code == 200
        yield {"client": client, "registry": registry_state, "local": local_state, "log": docker_log,
               "settings": settings}


def wait_job(client, job_id, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("success", "failed", "cancelled"):
            return job
        time.sleep(0.2)
    raise AssertionError("构建超时")


def create_app_from_dockerfile(client, **overrides):
    imported = client.post("/api/dockerfile/import", json={"text": DOCKERFILE}).json()
    assert imported["base_image"] == "swr.mock/metaerp_sdk/java:8-huawei-r30-arm64"
    assert imported["target_name"] == "cnnc-coding-chc-fin.jar"
    assert imported["chown"] == "nonroot:nonroot"
    assert "{{COPY_FILES}}" in imported["full_template"]
    payload = {"name": "chc-fin", "repository": "cnnc-coding-chc-fin", "organization": "metaerp-beta15",
               "base_image": imported["base_image"], "target_dir": imported["target_dir"],
               "target_name": imported["target_name"], "chown": imported["chown"],
               "full_template": imported["full_template"], "platform": "linux/arm64"}
    payload.update(overrides)
    resp = client.post("/api/apps", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def build(client, app_id, name="cnnc-coding-fin-1.0.0-SNAPSHOT.jar", content=b"jar-content", **form):
    data = {"app_id": str(app_id), "operator": "tester", **form}
    resp = client.post("/api/builds", data=data, files=[("files", (name, content, "application/java-archive"))])
    assert resp.status_code == 201, resp.text
    return wait_job(client, resp.json()["id"])


def test_info_and_registry(env):
    client = env["client"]
    icon = client.get("/favicon.svg")
    assert icon.status_code in (200, 204)
    if icon.status_code == 200:
        assert icon.headers["content-type"].startswith("image/svg+xml")
    info = client.get("/api/info").json()
    assert info["registry"]["configured"] is True
    assert client.post("/api/registry/test").json()["ok"] is True
    assert client.get("/").status_code == 200


def test_incremental_build_flow(env):
    client = env["client"]
    app = create_app_from_dockerfile(client)

    versions = client.get(f"/api/apps/{app['id']}/versions").json()
    assert versions["latest"] == "1.0.7"
    assert versions["next_tag"] == "1.0.8"
    assert versions["latest_info"]["layers"] == 8
    assert versions["latest_info"]["architecture"] == "arm64"
    assert versions["tags"][:3] == ["1.0.7", "1.0.6", "1.0.5"]

    preview = client.post(f"/api/apps/{app['id']}/preview",
                          json={"filenames": ["cnnc-coding-fin-1.0.0-SNAPSHOT.jar"]}).json()
    assert preview["dockerfile"].splitlines()[-2:] == [
        f"FROM swr.mock/{REPO}:1.0.7",
        'COPY --chown=nonroot:nonroot ["files/", "/app/"]',
    ]
    assert preview["files"][0]["dest"] == "/app/cnnc-coding-chc-fin.jar"

    job = build(client, app["id"])
    assert job["status"] == "success", job["error"]
    assert (job["mode"], job["prev_tag"], job["new_tag"]) == ("incremental", "1.0.7", "1.0.8")
    assert job["digest"] == "sha256:" + "ab" * 32
    local = json.loads(env["local"].read_text())
    built = local[f"swr.mock/{REPO}:1.0.8"]
    assert built["base"] == f"swr.mock/{REPO}:1.0.7"
    assert built["files"] == ["cnnc-coding-chc-fin.jar"]
    assert built["arch"] == "arm64"
    assert load_state(env["registry"])["repos"][REPO]["1.0.8"]["layers"] == 9

    log = client.get(f"/api/jobs/{job['id']}/log").json()
    assert log["finished"] and "[4/4]" in log["text"] and PASSWORD not in log["text"]
    calls = [json.loads(l)["args"] for l in env["log"].read_text().splitlines()]
    assert ["login", "swr.mock", "-u", USER, "--password-stdin"] in calls
    assert ["rmi", f"swr.mock/{REPO}:1.0.7"] in calls

    # 第二次构建：自动基于刚推送的 1.0.8
    job2 = build(client, app["id"])
    assert (job2["status"], job2["prev_tag"], job2["new_tag"]) == ("success", "1.0.8", "1.0.9")

    # 全量构建：基于基础镜像
    job3 = build(client, app["id"], mode="full")
    assert (job3["status"], job3["mode"], job3["new_tag"]) == ("success", "full", "1.0.10")
    assert "FROM swr.mock/metaerp_sdk/java:8-huawei-r30-arm64" in job3["dockerfile"]
    assert "EXPOSE 8080" in job3["dockerfile"]

    # 指定版本号与基础版本
    job4 = build(client, app["id"], tag="2.0.0", base_tag="1.0.9")
    assert (job4["status"], job4["base_tag"], job4["new_tag"]) == ("success", "1.0.9", "2.0.0")
    # 已存在的版本号不允许覆盖
    job5 = build(client, app["id"], tag="2.0.0")
    assert job5["status"] == "failed" and "已存在" in job5["error"]

    jobs = client.get("/api/jobs", params={"app_id": app["id"]}).json()
    assert jobs["total"] == 5


def test_arch_mismatch_stops_before_push(env):
    client = env["client"]
    app = create_app_from_dockerfile(client, platform="")  # 未设置构建平台，x86 构建机会构建出 amd64
    job = build(client, app["id"])
    assert job["status"] == "failed"
    assert "架构不一致" in job["error"]
    assert "1.0.8" not in load_state(env["registry"])["repos"][REPO]
    calls = [json.loads(l)["args"][0] for l in env["log"].read_text().splitlines()]
    assert "push" not in calls


def test_layer_limit_switches_to_full(env):
    client = env["client"]
    app = create_app_from_dockerfile(client, max_layers=8)
    job = build(client, app["id"])
    assert job["status"] == "success", job["error"]
    assert job["mode"] == "full"
    assert any("累加上限" in n for n in job["notes"])


def test_first_build_on_empty_repo_and_zip(env):
    client = env["client"]
    app = create_app_from_dockerfile(client, name="new-app", repository="brand-new-app", target_name="")
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("lib/a.jar", b"a")
        zf.writestr("config/application.yml", b"x: 1")
    job = build(client, app["id"], name="bundle.zip", content=buf.getvalue(), extract_zip="true")
    assert job["status"] == "success", job["error"]
    assert (job["mode"], job["new_tag"]) == ("full", "1.0.0")
    built = json.loads(env["local"].read_text())["swr.mock/metaerp-beta15/brand-new-app:1.0.0"]
    assert built["files"] == ["config/application.yml", "lib/a.jar"]


def test_login_failure(env, monkeypatch):
    client = env["client"]
    app = create_app_from_dockerfile(client)
    monkeypatch.setenv("FAKE_DOCKER_PASSWORD", "other")
    job = build(client, app["id"])
    assert job["status"] == "failed" and "docker login 失败" in job["error"]


def test_validation(env):
    client = env["client"]
    resp = client.post("/api/apps", json={"name": "x", "repository": "Bad Name"})
    assert resp.status_code == 400
    app = create_app_from_dockerfile(client)
    resp = client.post("/api/builds", data={"app_id": str(app["id"]), "tag": "-bad"},
                       files=[("files", ("a.jar", b"1", "application/octet-stream"))])
    assert resp.status_code == 400
