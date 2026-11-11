"""环境档案、负载均衡入口、分层配置集与证书。"""
import base64

import pytest
from fastapi.testclient import TestClient

from swr_builder.config import Settings
from swr_builder.main import create_app
from swr_builder.security import hash_password
from swr_builder.services.configsets import parse_properties
from tests.test_accounts_settings import HEAD, login

PASSWORD = "UserPass123"


@pytest.fixture
def env(tmp_path):
    settings = Settings()
    settings.build.data_dir = str(tmp_path / "data")
    settings.server.admin_password = "AdminPass123"
    app = create_app(settings)
    with TestClient(app, headers=HEAD) as client:
        login(client)
        store = app.state.store
        project = store.create_project("orders", "订单", [])["id"]
        cluster = store.create_cluster({"name": "cce-a", "auth_type": "sa_token", "server": "https://k.example",
                                        "credential_encrypted": "x"})
        sit = store.create_namespace(cluster, "orders-sit", "test", {}, project)
        uat = store.create_namespace(cluster, "orders-uat", "staging", {}, project)
        yield {"app": app, "client": client, "store": store, "project": project, "sit": sit, "uat": uat}


def user_client(app, store, name, role, scope_type, scope_id):
    user = store.create_user(username=name, password_hash=hash_password(PASSWORD), role="member", enabled=1,
                             must_change_password=0)
    store.create_binding("user", user["id"], role, scope_type, scope_id, None)
    client = TestClient(app, headers=HEAD)
    assert login(client, name, PASSWORD).status_code == 200
    return client


def create_set(client, scope_type, scope_id, kind, entries, name=None):
    body = {"scope_type": scope_type, "scope_id": scope_id, "kind": kind, "entries": entries}
    if name:
        body["name"] = name
    return client.post("/api/config-sets", json=body)


# ------------------------------------------------------------------ 环境档案与负载均衡入口
def test_environment_profile_defaults_validation_and_edit_conflicts(env):
    c, sit = env["client"], env["sit"]
    listed = c.get("/api/environments").json()
    assert {e["name"] for e in listed["items"]} == {"orders-sit", "orders-uat"}
    detail = c.get(f"/api/environments/{sit}").json()
    assert detail["profile_saved"] is False and detail["profile_version"] == 0
    assert detail["profile"]["naming"]["deployment"] == "{base}-app" and detail["profile"]["host"] == "{base}-app.{namespace}"

    elb = c.post("/api/ingress-profiles", json={"name": "SIT 共享 ELB", "spec": {
        "elb_id": "3a57f337-0000-0000-0000-000000000000", "port": 443, "certificate_ids": ["4d60e90005a0475e899f19db620ffda9"]}})
    assert elb.status_code == 201, elb.text
    elb = elb.json()
    saved = c.put(f"/api/environments/{sit}/profile", json={
        "base_version": 0, "ingress_profile_id": elb["id"],
        "profile": {"scheduling": {"node_pool": "common-nodepool"}, "log": {"enabled": True, "host_path": "/data01/log",
                                                                           "mount_path": "/applog"}}})
    assert saved.status_code == 200, saved.text
    saved = saved.json()
    assert saved["profile_version"] == 1 and saved["ingress_profile"]["name"] == "SIT 共享 ELB"
    assert saved["profile"]["scheduling"] == {"node_pool_label": "cce.cloud.com/cce-nodepool", "node_pool": "common-nodepool",
                                              "anti_affinity": True, "zone_spread": True}
    stale = c.put(f"/api/environments/{sit}/profile", json={"base_version": 0, "profile": {}})
    assert stale.status_code == 409 and stale.json()["code"] == "REVISION_CONFLICT"
    bad = c.put(f"/api/environments/{sit}/profile", json={"base_version": 1, "profile": {"naming": {"service": "{base}_SVC"}}})
    assert bad.status_code == 400
    reserved = c.post("/api/ingress-profiles", json={"name": "x", "spec": {
        "elb_id": "abc", "annotations": {"kubernetes.io/elb.listener-master-ingress": "a/b"}}})
    assert reserved.status_code == 400 and "CCE" in reserved.json()["detail"]
    in_use = c.delete(f"/api/ingress-profiles/{elb['id']}")
    assert in_use.status_code == 409


# ------------------------------------------------------------------ 分层配置
def test_layered_env_config_merge_locks_secrets_and_permissions(env):
    c, app, store, project, sit = env["client"], env["app"], env["store"], env["project"], env["sit"]
    assert create_set(c, "platform", 0, "env", [{"key": "JAVA_OPTS", "value": "-Xmx1g"},
                                               {"key": "APM_ENABLED", "value": "true", "locked": True}]).status_code == 201
    assert create_set(c, "project", project, "env", [{"key": "APP_ID", "value": "orders"}]).status_code == 201
    locked = create_set(c, "environment", sit, "env", [{"key": "APM_ENABLED", "value": "false"}])
    assert locked.status_code == 400 and "锁定" in locked.json()["detail"]
    created = create_set(c, "environment", sit, "env", [{"key": "JAVA_OPTS", "value": "-Xmx2g"},
                                                        {"key": "spring.profiles.active", "value": "sit"}])
    assert created.status_code == 201, created.text
    secret = create_set(c, "environment", sit, "secret_env", [{"key": "DB_PASSWORD", "value": "s3cret"},
                                                              {"key": "SIGN_KEY", "value": "k1"}]).json()
    assert secret["name"] == "secret-env" and secret["entries"][0] == {
        "key": "DB_PASSWORD", "description": "", "locked": False, "secret": True, "source": "value", "size": 6}
    assert "s3cret" not in str(secret)

    effective = c.get(f"/api/environments/{sit}/effective-config", params={"project_id": project}).json()
    env_values = {e["key"]: (e["value"], e["source"]["scope_type"]) for e in effective["env"]}
    assert env_values == {"JAVA_OPTS": ("-Xmx2g", "environment"), "APM_ENABLED": ("true", "platform"),
                          "APP_ID": ("orders", "project"), "spring.profiles.active": ("sit", "environment")}
    java = next(e for e in effective["env"] if e["key"] == "JAVA_OPTS")
    assert [o["scope_type"] for o in java["overrides"]] == ["platform"]
    assert [e["key"] for e in effective["secret_env"]] == ["DB_PASSWORD", "SIGN_KEY"]

    revealed = c.post(f"/api/config-sets/{secret['id']}/reveal", json={"keys": ["DB_PASSWORD"]}).json()
    assert revealed == {"values": {"DB_PASSWORD": {"value": "s3cret", "encoding": "text"}}}
    assert store.list_audit(action="config.secret.view")[1] == 1

    updated = c.put(f"/api/config-sets/{secret['id']}", json={"base_version": 1, "note": "换签名密钥", "entries": [
        {"key": "DB_PASSWORD", "keep": True}, {"key": "SIGN_KEY", "value": "k2"}]})
    assert updated.status_code == 200 and updated.json()["version"] == 2
    changes = c.get(f"/api/config-sets/{secret['id']}/diff", params={"from": 1, "to": 2}).json()["items"]
    assert changes == [{"key": "SIGN_KEY", "change": "changed"}]
    assert c.post(f"/api/config-sets/{secret['id']}/reveal", json={}).json()["values"]["DB_PASSWORD"]["value"] == "s3cret"
    stale = c.put(f"/api/config-sets/{secret['id']}", json={"base_version": 1, "entries": []})
    assert stale.status_code == 409

    viewer = user_client(app, store, "viewer", "namespace_viewer", "namespace", sit)
    seen = viewer.get("/api/config-sets", params={"scope_type": "environment", "scope_id": sit}).json()["items"]
    assert {s["kind"] for s in seen} == {"env", "secret_env"} and not any(s["can_edit"] or s["can_reveal"] for s in seen)
    assert viewer.post(f"/api/config-sets/{secret['id']}/reveal", json={}).status_code == 403
    assert viewer.put(f"/api/config-sets/{secret['id']}", json={"base_version": 2, "entries": []}).status_code == 403
    assert viewer.get("/api/config-sets", params={"scope_type": "environment", "scope_id": env["uat"]}).status_code == 404

    owner = user_client(app, store, "owner", "namespace_owner", "namespace", sit)
    assert owner.post(f"/api/config-sets/{secret['id']}/reveal", json={}).status_code == 200
    assert create_set(owner, "platform", 0, "env", [{"key": "X", "value": "1"}]).status_code == 403


def test_environment_compare_and_properties_files(env):
    c, project, sit, uat = env["client"], env["project"], env["sit"], env["uat"]
    common = "# 公共配置\nIAM_ENDPOINT=https://iam.example\nGATEWAY = https://gw.sit.example\nTIMEOUT: 30\n"
    create_set(c, "environment", sit, "env", [{"key": "JAVA_OPTS", "value": "-Xmx2g"}, {"key": "ENV", "value": "sit"}])
    create_set(c, "environment", uat, "env", [{"key": "JAVA_OPTS", "value": "-Xmx2g"}, {"key": "ENV", "value": "uat"},
                                              {"key": "EXTRA", "value": "1"}])
    for namespace, gateway in ((sit, "https://gw.sit.example"), (uat, "https://gw.uat.example")):
        files = create_set(c, "environment", namespace, "files", [
            {"key": "application.properties", "value": "spring.profiles.include=iac,global\n"},
            {"key": "application-global.properties", "value": common.replace("https://gw.sit.example", gateway)}],
            name="production-deploy-config")
        assert files.status_code == 201, files.text
        assert files.json()["entries"][1]["format"] == "properties"
    result = c.get("/api/environments/compare", params={"left": sit, "right": uat, "project_id": project}).json()
    env_rows = {r["key"]: r["status"] for r in result["env"]}
    assert env_rows == {"JAVA_OPTS": "same", "ENV": "different", "EXTRA": "right_only"}
    files = result["files"][0]
    assert files["name"] == "production-deploy-config" and files["status"] == "different"
    global_file = next(f for f in files["files"] if f["key"] == "application-global.properties")
    assert global_file["properties"] == [{"key": "GATEWAY", "left": "https://gw.sit.example",
                                          "right": "https://gw.uat.example", "status": "different"}]


def test_parse_properties_handles_separators_comments_and_continuations():
    text = "# c\n! c2\na=1\nb : 2\nc 3\nd=x\\\n  y\n\ne=\nf\n"
    assert parse_properties(text) == [("a", "1"), ("b", "2"), ("c", "3"), ("d", "xy"), ("e", ""), ("f", "")]


# ------------------------------------------------------------------ 证书
def _upload(client, name, filename, content, **extra):
    return client.post("/api/certificates", json={"name": name, "filename": filename,
                                                  "content_base64": base64.b64encode(content).decode(), **extra})


def test_key_files_jks_and_references_from_secret_files(env):
    c, app, store, sit = env["client"], env["app"], env["store"], env["sit"]
    jks = _upload(c, "Kafka 信任库", "kafka.client.truststore.jks", b"\xfe\xed\xfe\xed" + b"\x00" * 60,
                  not_after="2027-06-30")
    assert jks.status_code == 201, jks.text
    jks = jks.json()
    assert jks["kind"] == "jks" and jks["not_after"] == "2027-06-30T00:00:00+00:00" and jks["status"] in ("ok", "warning")
    root_key = _upload(c, "根密钥 1", "1.key", bytes(range(90))).json()
    assert root_key["kind"] == "file" and root_key["status"] == "unknown"

    bundle = create_set(c, "environment", sit, "secret_files", [
        {"key": "kafka.client.truststore.jks", "certificate_id": jks["id"]},
        {"key": "1.key", "certificate_id": root_key["id"]}], name="certificate-secret")
    assert bundle.status_code == 201, bundle.text
    bundle = bundle.json()
    assert bundle["entries"][0]["certificate_name"] == "Kafka 信任库" and bundle["entries"][0]["certificate_version"] == 1
    env_ref = create_set(c, "environment", sit, "secret_env", [{"key": "TRUSTSTORE", "certificate_id": jks["id"]}])
    assert env_ref.status_code == 400  # 二进制文件不能作为环境变量

    detail = c.get(f"/api/certificates/{jks['id']}").json()
    assert detail["used_by"] == 1 and detail["usage"][0]["name"] == "certificate-secret"
    assert c.delete(f"/api/certificates/{jks['id']}").status_code == 409

    rotated = c.post(f"/api/certificates/{jks['id']}/versions", json={
        "base_version": 1, "filename": "kafka.client.truststore.jks",
        "content_base64": base64.b64encode(b"\xfe\xed\xfe\xed" + b"\x01" * 60).decode()})
    assert rotated.status_code == 201 and rotated.json()["version"] == 2
    revealed = c.post(f"/api/config-sets/{bundle['id']}/reveal", json={"keys": ["kafka.client.truststore.jks"]}).json()
    assert revealed["values"]["kafka.client.truststore.jks"]["encoding"] == "base64"  # 仍引用第 1 版

    content = c.get(f"/api/certificates/{root_key['id']}/versions/1/content").json()
    assert base64.b64decode(content["content_base64"]) == bytes(range(90))
    assert store.list_audit(action="certificate.download")[1] == 1
    viewer = user_client(app, store, "cert-viewer", "auditor", "global", 0)
    assert viewer.get("/api/certificates").status_code == 200
    assert viewer.get(f"/api/certificates/{root_key['id']}/versions/1/content").status_code == 403


def test_x509_certificates_are_parsed_and_expiry_is_reported(env):
    x509 = pytest.importorskip("cryptography.x509")
    from datetime import datetime, timedelta, timezone

    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "orders.example")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(4242).not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=20))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("orders.example")]), critical=False)
            .sign(key, hashes.SHA256()))
    pem = cert.public_bytes(serialization.Encoding.PEM)
    c = env["client"]
    uploaded = _upload(c, "订单服务端证书", "tls.crt", pem).json()
    assert uploaded["kind"] == "x509" and uploaded["status"] == "expiring" and 18 <= uploaded["days_left"] <= 20
    assert uploaded["metadata"]["subject"] == "CN=orders.example" and uploaded["metadata"]["san"] == ["orders.example"]
    assert uploaded["metadata"]["serial"] == format(4242, "x") and uploaded["metadata"]["self_signed"] is True
    key_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
    private = _upload(c, "订单服务端私钥", "tls.key", key_pem).json()
    assert private["kind"] == "key" and private["metadata"]["encrypted"] is False
    tls = create_set(c, "environment", env["sit"], "secret_env", [{"key": "tls.crt", "certificate_id": uploaded["id"]},
                                                                  {"key": "tls.key", "certificate_id": private["id"]}])
    assert tls.status_code == 201, tls.text
    values = c.post(f"/api/config-sets/{tls.json()['id']}/reveal", json={}).json()["values"]
    assert values["tls.crt"]["value"].startswith("-----BEGIN CERTIFICATE-----")
