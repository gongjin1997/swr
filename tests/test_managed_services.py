"""托管服务：渲染、预览与服务端校验、发布快照、Server-Side Apply 下发、回滚、HPA 副本数移交、清理与运行状态。"""
import base64
import time

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient

from swr_builder.config import Settings
from swr_builder.delivery.render import ConfigObject, RenderInputs, render
from swr_builder.delivery.spec import default_spec, normalize_managed_spec
from swr_builder.main import create_app
from swr_builder.security import hash_password
from swr_builder.services.environments import merged_profile
from tests.fake_kube import FakeKube
from tests.test_accounts_settings import HEAD, login

PASSWORD = "UserPass123"
DIGEST_1, DIGEST_2 = "sha256:" + "1" * 64, "sha256:" + "2" * 64
POLICY = {"require_approval": False, "allowed_registry_orgs": ["team"], "service_types": ["ClusterIP", "NodePort"],
          "ingress": {"enabled": True, "host_suffixes": [], "ingress_class": ""}, "hpa_enabled": True, "max_replicas": 6}
FINAL = ("success", "failed", "unknown", "rejected", "cancelled", "expired")


def wait(client, deployment_id):
    for _ in range(300):
        record = client.get(f"/api/deployments/{deployment_id}").json()
        if record["status"] in FINAL:
            return record
        time.sleep(0.03)
    raise AssertionError("发布超时")


def secret_value(obj, key):
    return base64.b64decode(obj["data"][key]).decode()


@pytest.fixture
def env(tmp_path):
    settings = Settings()
    settings.build.data_dir = str(tmp_path / "data")
    settings.server.admin_password = "AdminPass123"
    kube = FakeKube(["orders-sit"])
    app = create_app(settings, deploy_transport=httpx.MockTransport(kube))
    with TestClient(app, headers=HEAD) as client:
        login(client)
        store = app.state.store
        project = client.post("/api/projects", json={"slug": "orders", "name": "订单",
                                                    "registry_orgs": [{"namespace": "team", "base_prefixes": []}]}).json()
        cluster = client.post("/api/clusters", json={"name": "cce-a", "server": "https://kube.example",
                                                    "auth_type": "sa_token", "credential": {"token": "cluster-token"}}).json()
        namespace = client.post("/api/namespaces", json={"name": "orders-sit", "cluster_id": cluster["id"], "tier": "test",
                                                        "project_id": project["id"], "policy": POLICY})
        assert namespace.status_code == 201, namespace.text
        namespace = namespace.json()
        elb = client.post("/api/ingress-profiles", json={"name": "SIT 入口", "spec": {
            "elb_id": "00000000-0000-4000-8000-0000000000e1", "port": 443, "certificate_ids": ["cert-a"],
            "url_match_mode": "STARTS_WITH"}}).json()
        saved = client.put(f"/api/environments/{namespace['id']}/profile", json={
            "base_version": 0, "ingress_profile_id": elb["id"], "profile": {
                "scheduling": {"node_pool": "pool-a"}, "image_pull_secret": "pull-secret",
                "log": {"enabled": True, "host_path": "/var/log/apps", "mount_path": "/var/log/app",
                        "sub_path_expr": "$(POD_NAMESPACE)/$(POD_NAME)"},
                "defaults": {"replicas": 2, "cpu_request": "250m", "memory_request": "512Mi", "cpu_limit": "1",
                             "memory_limit": "1Gi"}}})
        assert saved.status_code == 200, saved.text
        scope = {"scope_type": "environment", "scope_id": namespace["id"]}
        sets = {}
        for kind, entries, name in [
                ("env", [{"key": "SHARED_FLAG", "value": "on"}, {"key": "REGION_CODE", "value": "r1"}], None),
                ("secret_env", [{"key": "DB_PASSWORD", "value": "db-secret-1"}], None),
                ("files", [{"key": "app.properties", "value": "timeout=30\n"}, {"key": "other.properties", "value": "x=1\n"}],
                 "shared-config"),
                ("secret_files", [{"key": "ca.pem", "value": "trusted-ca-content"}], "trust-store")]:
            body = {**scope, "kind": kind, "entries": entries}
            if name:
                body["name"] = name
            response = client.post("/api/config-sets", json=body)
            assert response.status_code == 201, response.text
            sets[kind] = response.json()
        app_row = store.create_app({"name": "order-api", "repository": "order-api", "organization": "team"}, project["id"])
        jobs = [store.create_job(app_id=app_row["id"], app_name="order-api", project_id=project["id"], status="success",
                                 image=f"swr.example/team/order-api:1.0.{i}", digest=digest,
                                 created_at="2026-11-10T00:00:00+00:00") for i, digest in enumerate((DIGEST_1, DIGEST_2))]
        yield {"app": app, "client": client, "store": store, "kube": kube, "project": project, "namespace": namespace,
               "app_row": app_row, "jobs": jobs, "sets": sets, "elb": elb}


def managed_spec(**overrides):
    spec = default_spec(
        container={"name": "", "env": [{"name": "GRAY", "value": "false"}], "downward_api": True,
                   "image_pull_policy": "IfNotPresent"},
        probe={"type": "http", "scheme": "HTTPS", "path": "/actuator/health", "port": 8081},
        service={"enabled": True, "type": "NodePort", "ports": [{"name": "http", "port": 8080, "target_port": 8080},
                                                               {"name": "https", "port": 8443, "target_port": 8443}]},
        ingress={"enabled": True, "host": "", "path": "/", "port": 8080, "url_match_mode": ""},
        mounts=[{"config": "trust-store", "path": "/opt/trust", "items": [], "volume": "trust"},
                {"config": "shared-config", "path": "/app/config", "items": ["app.properties"], "volume": "shared"},
                {"config": "properties", "path": "/app/config", "items": ["custom.properties"], "volume": "own"}],
        log={"enabled": True, "volume": "logs"}, pod_annotations={"team.example/owner": "orders"})
    spec.update(overrides)
    return spec


def create_service(env, name="order-api", spec=None, config=None):
    body = {"name": name, "project_id": env["project"]["id"], "namespace_id": env["namespace"]["id"],
            "app_id": env["app_row"]["id"], "spec": spec or managed_spec(),
            "config": config if config is not None else {
                "env": [{"key": "OWN_KEY", "value": "own"}, {"key": "REGION_CODE", "value": "r-own"}],
                "secret_env": [{"key": "API_TOKEN", "value": "token-value-1"}],
                "files": [{"key": "custom.properties", "value": "feature=a\n"}]}}
    response = env["client"].post("/api/services", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def deploy(env, service_id, job_index=0, latest=True, key=None):
    response = env["client"].post(f"/api/services/{service_id}/deployments",
                                  json={"job_id": env["jobs"][job_index], "use_latest_config": latest},
                                  headers={"Idempotency-Key": key or f"deploy-{service_id}-{time.time_ns()}"})
    assert response.status_code == 202, response.text
    return wait(env["client"], response.json()["id"])


# ------------------------------------------------------------------ 渲染（纯函数）
def test_render_produces_template_shaped_objects_in_apply_order():
    namespace = {"name": "orders-sit", "policy": POLICY}
    spec = normalize_managed_spec(managed_spec(hpa={"enabled": True, "min_replicas": 2, "max_replicas": 4,
                                                   "cpu_utilization": 65}), namespace)
    profile = merged_profile({"scheduling": {"node_pool": "pool-a"}, "image_pull_secret": "pull-secret",
                              "log": {"enabled": True, "host_path": "/var/log/apps", "mount_path": "/var/log/app",
                                      "sub_path_expr": "$(POD_NAME)"}})
    ingress = {"ingress_class": "cce", "elb_class": "performance", "elb_id": "elb-1", "port": 443,
               "certificate_ids": ["c1", "c2"], "annotations": {}, "url_match_mode": "STARTS_WITH"}
    inputs = RenderInputs(
        service_id=7, service_name="order-api", namespace="orders-sit", spec=spec, profile=profile, ingress=ingress,
        image="swr.example/team/order-api@" + DIGEST_1, env={"B": "2", "A": "1"}, secret_env={"PASS": b"p"},
        mounts={"trust-store": ConfigObject("trust-store", True, {"ca.pem": b"ca"}, True,
                                            {"set_id": 3, "version": 2}),
                "shared-config": ConfigObject("shared-config", False, {"app.properties": b"a=1"}, True,
                                              {"set_id": 4, "version": 5}),
                "properties": ConfigObject("order-api-properties", False, {"custom.properties": b"c=1"}, False)},
        config_hash="abc123", revision_id=11, deployment_id=21)
    objects = render(inputs)
    assert [(o["kind"], o["metadata"]["name"]) for o in objects] == [
        ("ConfigMap", "shared-config"), ("Secret", "trust-store"), ("ConfigMap", "order-api-configmap"),
        ("Secret", "order-api-secrets"), ("ConfigMap", "order-api-properties"), ("Service", "order-api-service"),
        ("Deployment", "order-api-app"), ("Ingress", "order-api-ingress"), ("HorizontalPodAutoscaler", "order-api-app")]
    shared = objects[0]
    assert shared["metadata"]["labels"] == {"app.kubernetes.io/managed-by": "swr-builder", "swr-builder/config-set": "4"}
    assert shared["metadata"]["annotations"] == {"swr-builder/config-version": "5"}
    assert objects[2]["data"] == {"A": "1", "B": "2"} and objects[3]["data"] == {"PASS": base64.b64encode(b"p").decode()}
    assert objects[4]["metadata"]["labels"]["swr-builder/service"] == "7"
    deployment = objects[6]
    assert "replicas" not in deployment["spec"]  # HPA 管理副本数
    assert deployment["spec"]["selector"] == {"matchLabels": {"app": "order-api-app"}}
    pod = deployment["spec"]["template"]
    assert pod["metadata"]["annotations"] == {"swr-builder/config-hash": "abc123", "swr-builder/deployment-id": "21",
                                              "swr-builder/revision": "11", "team.example/owner": "orders"}
    container = pod["spec"]["containers"][0]
    assert container["name"] == "order-api-app" and container["image"].endswith(DIGEST_1)
    assert container["envFrom"] == [{"configMapRef": {"name": "order-api-configmap"}},
                                    {"secretRef": {"name": "order-api-secrets"}}]
    assert [e["name"] for e in container["env"]] == ["GRAY", "HOST_IP", "POD_NAME", "POD_NAMESPACE", "POD_IP"]
    assert container["readinessProbe"] == {"httpGet": {"path": "/actuator/health", "port": 8081, "scheme": "HTTPS"},
                                           "periodSeconds": 15, "timeoutSeconds": 5, "failureThreshold": 3,
                                           "successThreshold": 1}
    assert container["startupProbe"]["failureThreshold"] == 30 and container["livenessProbe"]["periodSeconds"] == 30
    assert container["securityContext"] == {"runAsUser": 65532, "runAsGroup": 65532, "runAsNonRoot": True,
                                            "allowPrivilegeEscalation": False}
    assert container["resources"] == {"requests": {"cpu": "500m", "memory": "4Gi"}, "limits": {"cpu": "2", "memory": "4Gi"}}
    assert container["volumeMounts"] == [
        {"name": "trust", "mountPath": "/opt/trust"},
        {"name": "shared", "mountPath": "/app/config/app.properties", "subPath": "app.properties"},
        {"name": "own", "mountPath": "/app/config/custom.properties", "subPath": "custom.properties"},
        {"name": "logs", "mountPath": "/var/log/app", "subPathExpr": "$(POD_NAME)"}]
    spec_pod = pod["spec"]
    assert spec_pod["volumes"][0] == {"name": "trust", "secret": {"secretName": "trust-store", "defaultMode": 420}}
    assert spec_pod["volumes"][3] == {"name": "logs", "hostPath": {"path": "/var/log/apps"}}
    assert spec_pod["imagePullSecrets"] == [{"name": "pull-secret"}] and spec_pod["automountServiceAccountToken"] is False
    assert spec_pod["affinity"]["nodeAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"]["nodeSelectorTerms"][0][
        "matchExpressions"] == [{"key": "cce.cloud.com/cce-nodepool", "operator": "In", "values": ["pool-a"]}]
    assert spec_pod["affinity"]["podAntiAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"][0]["topologyKey"] == \
        "kubernetes.io/hostname"
    assert spec_pod["topologySpreadConstraints"][0]["topologyKey"] == "topology.kubernetes.io/zone"
    service = objects[5]
    assert service["spec"]["type"] == "NodePort" and service["metadata"]["labels"]["app"] == "order-api-app"
    ingress_obj = objects[7]
    assert ingress_obj["metadata"]["annotations"] == {
        "kubernetes.io/elb.class": "performance", "kubernetes.io/elb.id": "elb-1", "kubernetes.io/elb.port": "443",
        "kubernetes.io/elb.tls-certificate-ids": "c1,c2"}
    rule = ingress_obj["spec"]["rules"][0]
    assert rule["host"] == "order-api-app.orders-sit" and ingress_obj["spec"]["ingressClassName"] == "cce"
    assert rule["http"]["paths"][0]["property"] == {"ingress.beta.kubernetes.io/url-match-mode": "STARTS_WITH"}
    assert objects[8]["spec"]["minReplicas"] == 2 and objects[8]["spec"]["scaleTargetRef"]["name"] == "order-api-app"


def test_spec_validation_against_namespace_policy():
    strict = {"name": "x", "policy": {"service_types": ["ClusterIP"], "hpa_enabled": False, "max_replicas": 3}}
    with pytest.raises(ValueError, match="NodePort"):
        normalize_managed_spec(managed_spec(), strict)
    with pytest.raises(ValueError, match="Ingress"):
        normalize_managed_spec(managed_spec(service=dict(default_spec()["service"])), strict)
    with pytest.raises(ValueError, match="副本数"):
        normalize_managed_spec(default_spec(replicas=5), strict)
    with pytest.raises(ValueError, match="swr-builder/"):
        normalize_managed_spec(default_spec(pod_annotations={"swr-builder/x": "1"}), strict)
    with pytest.raises(ValueError, match="重复"):
        normalize_managed_spec(default_spec(mounts=[{"config": "a", "path": "/x"}, {"config": "a", "path": "/x"}]), strict)
    with pytest.raises(ValueError, match="不支持的字段"):
        normalize_managed_spec(default_spec(unknown=1), strict)
    assert normalize_managed_spec(default_spec(), strict)["service"]["ports"][0]["target_port"] == 8080


# ------------------------------------------------------------------ 新建、预览、发布
def test_create_preview_deploy_and_snapshot_without_secret_values(env):
    client, kube, store = env["client"], env["kube"], env["store"]
    draft = client.post("/api/services/preview", json={
        "name": "order-api", "project_id": env["project"]["id"], "namespace_id": env["namespace"]["id"],
        "app_id": env["app_row"]["id"], "spec": managed_spec(), "dry_run": True,
        "config": {"secret_env": [{"key": "API_TOKEN", "value": "draft-secret"}],
                   "files": [{"key": "custom.properties", "value": "feature=a\n"}]}})
    assert draft.status_code == 200, draft.text
    preview = draft.json()
    assert not preview["errors"] and "draft-secret" not in draft.text and "db-secret-1" not in draft.text
    assert {r["action"] for r in preview["dry_run"]} == {"create"}
    assert [o["kind"] for o in yaml.safe_load_all(preview["yaml"])][-2:] == ["Deployment", "Ingress"]

    service = create_service(env)
    assert service["mode"] == "managed"
    rendered = client.post(f"/api/services/{service['id']}/render", json={"dry_run": True})
    assert rendered.status_code == 200, rendered.text
    assert "token-value-1" not in rendered.text and "（已隐藏" in rendered.json()["yaml"]
    record = deploy(env, service["id"])
    assert record["status"] == "success", record
    deployment = kube.get("deployments", "order-api-app", "orders-sit")
    assert deployment["spec"]["replicas"] == 2 and deployment["spec"]["template"]["spec"]["containers"][0]["image"] == \
        "swr.example/team/order-api@" + DIGEST_1
    own_env = kube.get("configmaps", "order-api-configmap", "orders-sit")["data"]
    assert own_env == {"OWN_KEY": "own", "REGION_CODE": "r-own", "SHARED_FLAG": "on"}  # 服务层覆盖环境层
    secrets = kube.get("secrets", "order-api-secrets", "orders-sit")
    assert secret_value(secrets, "API_TOKEN") == "token-value-1" and secret_value(secrets, "DB_PASSWORD") == "db-secret-1"
    assert secret_value(kube.get("secrets", "trust-store", "orders-sit"), "ca.pem") == "trusted-ca-content"
    assert kube.get("configmaps", "order-api-properties", "orders-sit")["data"] == {"custom.properties": "feature=a\n"}
    assert kube.get("ingresses", "order-api-ingress", "orders-sit")["spec"]["rules"][0]["host"] == "order-api-app.orders-sit"
    dry_runs = [r for r in kube.requests if r[0] == "PATCH" and r[2].get("dryRun") == "All"]
    writes = [r for r in kube.requests if r[0] == "PATCH" and not r[2].get("dryRun")]
    assert len(dry_runs) >= 8 and len(writes) == 8 and all(r[2]["fieldManager"] == "swr-builder" for r in writes)
    snapshot = client.get(f"/api/services/{service['id']}/manifest")
    assert snapshot.status_code == 200 and "token-value-1" not in snapshot.text and "db-secret-1" not in snapshot.text
    assert {c["kind"] for c in snapshot.json()["config"]} == {"env", "secret_env", "files", "secret_files"}
    detail = client.get(f"/api/services/{service['id']}").json()
    assert detail["config_status"] == {"deployed": True, "pending": False, "changes": [],
                                       "deployment_id": record["id"]} and detail["workload"] == "order-api-app"
    runtime = client.get(f"/api/services/{service['id']}/runtime").json()
    assert runtime["summary"]["state"] == "healthy" and runtime["summary"]["ready"] == 2
    assert runtime["pods"][0]["start_time"] and runtime["pods"][0]["restarts"] == 0 and runtime["pods"][0]["current"]
    assert runtime["replicasets"][0]["current"] and runtime["hpa"] is None
    assert any(a["address"].startswith("https://order-api-app.orders-sit") for a in runtime["access"])
    assert "cluster-token" not in str(runtime) and "token-value-1" not in str(runtime)
    status = client.post("/api/services/status", json={"ids": [service["id"], 999]}).json()["items"]
    assert status == [{"service_id": service["id"], "state": "healthy", "message": "2/2 个副本可用", "ready": 2,
                       "desired": 2, "image": "swr.example/team/order-api@" + DIGEST_1,
                       "deployment_id": str(record["id"])}]


def test_config_versions_are_pinned_and_rollback_restores_them(env):
    client, kube = env["client"], env["kube"]
    service = create_service(env)
    first = deploy(env, service["id"])
    env_set = env["sets"]["env"]
    updated = client.put(f"/api/config-sets/{env_set['id']}", json={
        "base_version": 1, "entries": [{"key": "SHARED_FLAG", "value": "off"}, {"key": "REGION_CODE", "value": "r1"}]})
    assert updated.status_code == 200, updated.text
    status = client.get(f"/api/services/{service['id']}").json()["config_status"]
    assert status["pending"] and status["changes"][0]["name"] == "env" and status["changes"][0]["current"] == 2
    hash_before = kube.get("deployments", "order-api-app", "orders-sit")["spec"]["template"]["metadata"]["annotations"][
        "swr-builder/config-hash"]
    # 只换镜像：沿用上次发布的配置快照
    image_only = deploy(env, service["id"], job_index=1, latest=False)
    assert image_only["status"] == "success", image_only
    live = kube.get("deployments", "order-api-app", "orders-sit")["spec"]["template"]
    assert live["metadata"]["annotations"]["swr-builder/config-hash"] == hash_before
    assert kube.get("configmaps", "order-api-configmap", "orders-sit")["data"]["SHARED_FLAG"] == "on"
    assert live["spec"]["containers"][0]["image"].endswith(DIGEST_2)
    latest = deploy(env, service["id"], job_index=1, latest=True)
    assert latest["status"] == "success"
    assert kube.get("configmaps", "order-api-configmap", "orders-sit")["data"]["SHARED_FLAG"] == "off"
    assert kube.get("deployments", "order-api-app", "orders-sit")["spec"]["template"]["metadata"]["annotations"][
        "swr-builder/config-hash"] != hash_before
    assert not client.get(f"/api/services/{service['id']}").json()["config_status"]["pending"]
    rollback = client.post(f"/api/services/{service['id']}/rollback", json={"target_deployment_id": first["id"]},
                           headers={"Idempotency-Key": "rollback-to-first"})
    assert rollback.status_code == 202, rollback.text
    assert wait(client, rollback.json()["id"])["status"] == "success"
    assert kube.get("configmaps", "order-api-configmap", "orders-sit")["data"]["SHARED_FLAG"] == "on"
    assert kube.get("deployments", "order-api-app", "orders-sit")["spec"]["template"]["spec"]["containers"][0][
        "image"].endswith(DIGEST_1)


def test_hpa_handover_prune_and_shared_config_never_downgrades(env):
    client, kube, store = env["client"], env["kube"], env["store"]
    service = create_service(env, spec=managed_spec(replicas=3))
    assert deploy(env, service["id"])["status"] == "success"
    assert kube.get("deployments", "order-api-app", "orders-sit")["spec"]["replicas"] == 3
    current = client.get(f"/api/services/{service['id']}").json()
    spec = dict(current["spec"], hpa={"enabled": True, "min_replicas": 2, "max_replicas": 5, "cpu_utilization": 60},
                ingress=dict(current["spec"]["ingress"], enabled=False))
    revision = client.put(f"/api/services/{service['id']}", json={"base_revision": current["current_revision_id"],
                                                                  "spec": spec})
    assert revision.status_code == 200, revision.text
    record = deploy(env, service["id"])
    assert record["status"] == "success", record
    assert kube.get("deployments", "order-api-app", "orders-sit")["spec"]["replicas"] == 3  # 没有被重置为 1
    assert kube.get("horizontalpodautoscalers", "order-api-app", "orders-sit")["spec"]["maxReplicas"] == 5
    assert kube.get("ingresses", "order-api-ingress", "orders-sit") is None  # 关闭 Ingress 后清理
    assert any(r[0] == "PATCH" and r[2].get("fieldManager") == "swr-builder-handover" for r in kube.requests)

    # 第二个服务发布了共享配置的新版本；之后第一个服务按旧快照重新发布，不降级共享配置
    files = env["sets"]["files"]
    assert client.put(f"/api/config-sets/{files['id']}", json={"base_version": 1, "entries": [
        {"key": "app.properties", "value": "timeout=60\n"}, {"key": "other.properties", "value": "x=1\n"}]}).status_code == 200
    second = create_service(env, name="billing-api", config={}, spec=managed_spec(base="billing-api", mounts=[
        {"config": "shared-config", "path": "/app/config", "items": ["app.properties"], "volume": "shared"}]))
    assert deploy(env, second["id"])["status"] == "success"
    assert kube.get("configmaps", "shared-config", "orders-sit")["metadata"]["annotations"]["swr-builder/config-version"] == "2"
    restart = client.post(f"/api/services/{service['id']}/restart", json={}, headers={"Idempotency-Key": "restart-first"})
    assert restart.status_code == 202, restart.text
    assert wait(client, restart.json()["id"])["status"] == "success"
    shared = kube.get("configmaps", "shared-config", "orders-sit")
    assert shared["data"]["app.properties"] == "timeout=60\n"
    log = client.get(f"/api/deployments/{restart.json()['id']}/log", params={"offset": 0}).json()["text"]
    assert "ConfigMap/shared-config：skip" in log


def test_server_validation_failure_writes_nothing(env):
    client, kube = env["client"], env["kube"]
    service = create_service(env)
    kube.fail[("PATCH", "ingresses", "order-api-ingress")] = (422, "spec.rules[0].host: Invalid value")
    record = deploy(env, service["id"])
    assert record["status"] == "failed" and "服务端校验未通过" in record["error"] and "Invalid value" in record["error"]
    assert not [r for r in kube.requests if r[0] == "PATCH" and not r[2].get("dryRun")]


def test_permissions_for_managed_services(env):
    app, store, client = env["app"], env["store"], env["client"]
    namespace_id = env["namespace"]["id"]
    users = {}
    for name, role in (("deployer1", "deployer"), ("viewer1", "namespace_viewer"), ("owner1", "namespace_owner")):
        user = store.create_user(username=name, password_hash=hash_password(PASSWORD), role="member", enabled=1,
                                 must_change_password=0)
        store.create_binding("user", user["id"], role, "namespace", namespace_id, None)
        users[name] = TestClient(app, headers=HEAD)
        assert login(users[name], name, PASSWORD).status_code == 200
    body = {"name": "order-api", "project_id": env["project"]["id"], "namespace_id": namespace_id,
            "app_id": env["app_row"]["id"], "spec": managed_spec()}
    assert users["deployer1"].post("/api/services", json=body).status_code == 403
    created = users["owner1"].post("/api/services", json={**body, "config": {"secret_env": [{"key": "K", "value": "v"}]}})
    assert created.status_code == 201, created.text
    service_id = created.json()["id"]
    assert users["viewer1"].post(f"/api/services/{service_id}/render", json={"dry_run": True}).status_code == 403
    assert users["viewer1"].post(f"/api/services/{service_id}/render", json={}).status_code == 200
    sets = users["viewer1"].get("/api/config-sets", params={"scope_type": "service", "scope_id": service_id}).json()["items"]
    secret_set = next(s for s in sets if s["kind"] == "secret_env")
    assert not secret_set["can_edit"] and not secret_set["can_reveal"]
    assert users["viewer1"].post(f"/api/config-sets/{secret_set['id']}/reveal", json={}).status_code == 403
    assert users["owner1"].post(f"/api/config-sets/{secret_set['id']}/reveal", json={}).json()["values"]["K"]["value"] == "v"
    deployer_sets = users["deployer1"].get("/api/config-sets", params={"scope_type": "service", "scope_id": service_id})
    assert next(s for s in deployer_sets.json()["items"] if s["kind"] == "secret_env")["can_edit"]
    effective = users["viewer1"].get(f"/api/services/{service_id}/effective-config").json()
    assert {e["key"]: e["origin"]["scope_type"] for e in effective["secret_env"]} == {"DB_PASSWORD": "environment",
                                                                                      "K": "service"}
    bad = client.post("/api/config-sets", json={"scope_type": "service", "scope_id": service_id, "kind": "files",
                                                "name": "other", "entries": []})
    assert bad.status_code == 400 and "properties" in bad.json()["detail"]
    duplicate = client.post("/api/services", json={**body, "name": "order-api-2", "spec": managed_spec(base="order-api")})
    assert duplicate.status_code == 409


# ------------------------------------------------------------------ 运行状态
def test_pod_status_follows_kubectl_rules():
    from swr_builder.services.discovery import deployment_state, pod_status, status_level

    def pod(**status):
        return {"metadata": {}, "spec": {"containers": [{"name": "c"}]}, "status": {"phase": "Running", **status}}

    waiting = pod(containerStatuses=[{"name": "c", "state": {"waiting": {"reason": "CrashLoopBackOff", "message": "back-off"}}}])
    assert pod_status(waiting) == ("CrashLoopBackOff", "back-off") and status_level("CrashLoopBackOff", False) == "error"
    init = pod(phase="Pending", initContainerStatuses=[{"state": {"terminated": {"exitCode": 0}}},
                                                       {"state": {"waiting": {"reason": "PodInitializing"}}}])
    init["spec"]["initContainers"] = [{}, {}]
    assert pod_status(init)[0] == "Init:1/2"
    killed = pod(containerStatuses=[{"state": {"terminated": {"exitCode": 137, "reason": "OOMKilled"}}}])
    assert pod_status(killed)[0] == "OOMKilled"
    deleting = pod(conditions=[{"type": "Ready", "status": "True"}])
    deleting["metadata"]["deletionTimestamp"] = "2026-11-11T08:00:00Z"
    assert pod_status(deleting)[0] == "Terminating" and status_level("Terminating", True) == "warning"
    assert status_level("Running", True) == "ok" and status_level("Running", False) == "warning"
    rolling = {"metadata": {"generation": 3}, "spec": {"replicas": 3},
               "status": {"observedGeneration": 3, "replicas": 4, "updatedReplicas": 3, "availableReplicas": 3}}
    assert deployment_state(rolling) == ("progressing", "1 个旧副本等待终止")
    stuck = {"metadata": {}, "spec": {}, "status": {"conditions": [
        {"type": "Progressing", "status": "False", "reason": "ProgressDeadlineExceeded", "message": "timed out"}]}}
    assert deployment_state(stuck) == ("failed", "timed out")


def test_service_runtime_reports_restarts_age_and_related_events(env):
    client, kube = env["client"], env["kube"]
    service = create_service(env)
    assert deploy(env, service["id"])["status"] == "success"
    pods = sorted(k for k in kube.objects if k[0] == "pods")
    crashing = kube.objects[pods[0]]
    crashing["status"]["conditions"] = [{"type": "Ready", "status": "False"}]
    crashing["status"]["containerStatuses"][0].update(
        ready=False, restartCount=5, state={"waiting": {"reason": "CrashLoopBackOff", "message": "back-off 5m0s"}},
        lastState={"terminated": {"reason": "Error", "exitCode": 1, "startedAt": "2026-11-11T08:10:00Z",
                                  "finishedAt": "2026-11-11T08:12:00Z"}})
    deployment = kube.get("deployments", "order-api-app", "orders-sit")
    deployment["status"]["availableReplicas"] = 1
    deployment["status"]["readyReplicas"] = 1
    events = [("Pod", crashing["metadata"]["name"], "Warning", "BackOff", "2026-11-11T08:12:30Z"),
              ("Deployment", "order-api-app", "Normal", "ScalingReplicaSet", "2026-11-11T08:00:05Z"),
              ("Pod", "someone-else", "Warning", "Failed", "2026-11-11T09:00:00Z")]
    for index, (kind, name, kind_type, reason, when) in enumerate(events):
        kube.put({"kind": "Event", "metadata": {"name": f"event-{index}", "namespace": "orders-sit"},
                  "involvedObject": {"kind": kind, "name": name}, "type": kind_type, "reason": reason,
                  "message": f"{reason} message", "count": 3, "lastTimestamp": when, "firstTimestamp": when,
                  "source": {"component": "kubelet"}})
    runtime = client.get(f"/api/services/{service['id']}/runtime").json()
    summary = runtime["summary"]
    assert summary["state"] == "degraded" and summary["message"] == "可用副本 1/2"
    assert summary["restarts"] == 5 and summary["warning_events"] == 1 and summary["pods"] == 2
    bad = next(p for p in runtime["pods"] if p["name"] == crashing["metadata"]["name"])
    assert bad["status"] == "CrashLoopBackOff" and bad["level"] == "error" and not bad["ready"]
    assert bad["restarts"] == 5 and bad["last_restart_at"] == "2026-11-11T08:12:00+00:00"
    assert bad["last_restart_reason"] == "Error" and bad["start_time"] == "2026-11-11T08:00:00+00:00"
    assert bad["containers"][0]["last_state"]["exit_code"] == 1 and bad["containers"][0]["digest"] == DIGEST_1
    assert [e["reason"] for e in runtime["events"]] == ["BackOff", "ScalingReplicaSet"]
    assert runtime["events"][0]["count"] == 3 and runtime["events"][0]["source"] == "kubelet"
    assert runtime["deployment"]["containers"][0]["probes"]["readiness"].startswith("HTTPS GET :8081/actuator/health")
    assert [s["name"] for s in runtime["services"]] == ["order-api-service"]
    assert runtime["ingresses"][0]["https"] and runtime["ingresses"][0]["rules"][0]["paths"][0]["service"] == \
        "order-api-service"
    listing = client.post("/api/services/status", json={"ids": [service["id"]]}).json()["items"][0]
    assert listing["state"] == "degraded" and listing["ready"] == 1 and listing["desired"] == 2
