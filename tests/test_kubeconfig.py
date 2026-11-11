import asyncio
import base64

import httpx
import pytest
import yaml
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient

from swr_builder.deploy.k8s_client import KubeClient
from swr_builder.deploy.kubeconfig import parse_kubeconfig
from swr_builder.main import create_app
from swr_builder.config import Settings
from tests.test_accounts_settings import HEAD, login


def _config(user, server="https://selected.example:6443"):
    return yaml.safe_dump({
        "apiVersion": "v1", "kind": "Config", "current-context": "work",
        "clusters": [
            {"name": "unused", "cluster": {"server": "https://unused.example"}},
            {"name": "prod", "cluster": {"server": server}},
        ],
        "users": [{"name": "deployer", "user": user}],
        "contexts": [{"name": "work", "context": {"cluster": "prod", "user": "deployer"}}],
    }, sort_keys=False)


def _client_certificate_data():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "kubeconfig-client")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=1))
            .sign(key, hashes.SHA256()))
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    key_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                 serialization.NoEncryption())
    encode = lambda value: base64.b64encode(value).decode("ascii")
    return encode(cert_pem), encode(key_pem)


def test_kubeconfig_selects_current_context_and_static_token():
    server, auth_type, credential = parse_kubeconfig(_config({"token": "chosen-token"}))
    assert server == "https://selected.example:6443"
    assert auth_type == "sa_token"
    assert credential["token"] == "chosen-token"


def test_kubeconfig_insecure_tls_flag_is_explicit():
    config = yaml.safe_load(_config({"token": "chosen-token"}))
    config["clusters"][1]["cluster"]["insecure-skip-tls-verify"] = True
    _, _, credential = parse_kubeconfig(yaml.safe_dump(config))
    assert credential["insecure_skip_tls_verify"] is True


def test_kubeconfig_client_certificate_data_creates_mtls_context():
    cert, key = _client_certificate_data()
    server, auth_type, credential = parse_kubeconfig(_config({
        "client-certificate-data": cert, "client-key-data": key,
    }))
    assert server == "https://selected.example:6443"
    assert auth_type == "client_cert"

    async def request():
        async def handler(_):
            return httpx.Response(200, json={"gitVersion": "v1.30.0"})

        async with KubeClient(server, credential, httpx.MockTransport(handler)) as kube:
            assert kube.auth_headers == {}
            return await kube.version()

    assert asyncio.run(request()) == "v1.30.0"


@pytest.mark.parametrize("user,needle", [
    ({"token": "x", "exec": {"command": "echo secret"}}, "exec 插件"),
    ({"auth-provider": {"name": "oidc"}}, "auth-provider"),
    ({"tokenFile": "/tmp/token"}, "tokenFile"),
    ({"client-certificate": "/tmp/client.crt", "client-key": "/tmp/client.key"}, "外部证书文件"),
    ({"username": "alice", "password": "secret"}, "用户名/密码"),
    ({"token": "x", "client-certificate-data": "YQ==", "client-key-data": "Yg=="}, "只能选择一种"),
    ({"client-certificate-data": "YQ=="}, "client-key-data"),
    ({}, "静态 token"),
])
def test_unsupported_or_ambiguous_kubeconfig_auth_fails_closed(user, needle):
    with pytest.raises(ValueError, match=needle):
        parse_kubeconfig(_config(user))


def test_kubeconfig_kind_context_and_duplicate_keys_are_validated():
    config = yaml.safe_load(_config({"token": "x"}))
    config["kind"] = "List"
    with pytest.raises(ValueError, match="kind: Config"):
        parse_kubeconfig(yaml.safe_dump(config))
    with pytest.raises(ValueError, match="YAML"):
        parse_kubeconfig(_config({"token": "x"}) + "kind: List\n")
    config["kind"] = "Config"
    config["current-context"] = "missing"
    with pytest.raises(ValueError, match="current-context"):
        parse_kubeconfig(yaml.safe_dump(config))


def test_cluster_api_imports_and_tests_kubeconfig_without_returning_secrets(tmp_path):
    seen = []

    def handler(request):
        seen.append(request)
        assert request.url.host == "selected.example"
        assert request.headers["authorization"] == "Bearer kubeconfig-secret"
        if request.url.path == "/version":
            return httpx.Response(200, json={"gitVersion": "v1.30.0"})
        return httpx.Response(404)

    settings = Settings()
    settings.server.admin_password = "AdminPass123"
    settings.build.data_dir = str(tmp_path / "data")
    app = create_app(settings, deploy_transport=httpx.MockTransport(handler))
    with TestClient(app, headers=HEAD) as client:
        login(client)
        created = client.post("/api/clusters", json={
            "name": "config-cluster", "cce_cluster_id": "",
            "config": _config({"token": "kubeconfig-secret"}),
        })
        assert created.status_code == 201, created.text
        cluster = created.json()
        assert cluster["server"] == "https://selected.example:6443"
        assert cluster["auth_type"] == "sa_token"
        assert "kubeconfig-secret" not in created.text
        record = app.state.store.get_cluster_record(cluster["id"])
        assert record["credential_encrypted"].startswith("enc:v2:")
        tested = client.post(f"/api/clusters/{cluster['id']}/test", json={})
        assert tested.status_code == 200 and tested.json()["version"] == "v1.30.0"
        assert len(seen) == 1
