"""导入已有服务：分析 kubectl 导出 → 确认写入平台 → 接管发布（沿用线上镜像 digest）。测试数据是虚构的。"""
import base64
import copy
import time

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient

from swr_builder.config import Settings
from swr_builder.delivery.importer import parse_documents
from swr_builder.main import create_app
from tests.fake_kube import FakeKube
from tests.test_accounts_settings import HEAD, login

NS = "orders-uat"
JKS = b"\xfe\xed\xfe\xed\x00\x00\x00\x02binary-trust-store"
OPEN_POLICY = {"require_approval": False, "allowed_registry_orgs": ["team"], "service_types": ["ClusterIP", "NodePort"],
               "ingress": {"enabled": True, "host_suffixes": [], "ingress_class": ""}, "hpa_enabled": True}


def b64(value):
    return base64.b64encode(value if isinstance(value, bytes) else value.encode()).decode()


def probe(kind, scheme="HTTP"):
    timings = {"startupProbe": (6, 30), "readinessProbe": (15, 3), "livenessProbe": (30, 3)}[kind]
    return {"httpGet": {"path": "/actuator/health", "port": 8081, "scheme": scheme}, "periodSeconds": timings[0],
            "timeoutSeconds": 5, "failureThreshold": timings[1], "successThreshold": 1}


def deployment(base, container=None, scheme="HTTP", affinity=True, replicas=2):
    name = f"{base}-app"
    pod = {
        "containers": [{
            "name": container or name, "image": f"swr.example/team/{base}:2.1", "imagePullPolicy": "Always",
            "env": [{"name": "GRAY", "value": "false"}] + [
                {"name": env, "valueFrom": {"fieldRef": {"apiVersion": "v1", "fieldPath": path}}}
                for env, path in (("HOST_IP", "status.hostIP"), ("POD_NAME", "metadata.name"),
                                  ("POD_NAMESPACE", "metadata.namespace"), ("POD_IP", "status.podIP"))],
            "envFrom": [{"configMapRef": {"name": f"{base}-configmap"}}, {"secretRef": {"name": f"{base}-secrets"}}],
            "resources": {"requests": {"cpu": "500m", "memory": "1Gi"}, "limits": {"cpu": "1", "memory": "1Gi"}},
            "securityContext": {"runAsUser": 65532, "runAsGroup": 65532, "runAsNonRoot": True,
                                "allowPrivilegeEscalation": False},
            **{kind: probe(kind, scheme) for kind in ("startupProbe", "readinessProbe", "livenessProbe")},
            "volumeMounts": [{"name": "trust", "mountPath": "/opt/trust"},
                             {"name": "shared", "mountPath": "/app/config/app.properties", "subPath": "app.properties"},
                             {"name": "shared", "mountPath": "/app/config/global.properties", "subPath": "global.properties"},
                             {"name": "own", "mountPath": "/app/config/custom.properties", "subPath": "custom.properties"},
                             {"name": "logs", "mountPath": "/var/log/app", "subPathExpr": "$(POD_NAMESPACE)/$(POD_NAME)"}],
            "stdin": True, "tty": True}],
        "volumes": [{"name": "trust", "secret": {"secretName": "trust-store", "defaultMode": 420}},
                    {"name": "shared", "configMap": {"name": "shared-config", "defaultMode": 420}},
                    {"name": "own", "configMap": {"name": f"{base}-properties", "defaultMode": 420}},
                    {"name": "logs", "hostPath": {"path": "/var/log/apps", "type": ""}}],
        "imagePullSecrets": [{"name": "pull-secret"}], "automountServiceAccountToken": False,
        "terminationGracePeriodSeconds": 60,
    }
    if affinity:
        pod["affinity"] = {
            "nodeAffinity": {"requiredDuringSchedulingIgnoredDuringExecution": {"nodeSelectorTerms": [
                {"matchExpressions": [{"key": "cce.cloud.com/cce-nodepool", "operator": "In", "values": ["pool-a"]}]}]}},
            "podAntiAffinity": {"requiredDuringSchedulingIgnoredDuringExecution": [{
                "labelSelector": {"matchExpressions": [{"key": "app", "operator": "In", "values": [name]}]},
                "topologyKey": "kubernetes.io/hostname"}]}}
        pod["topologySpreadConstraints"] = [{"maxSkew": 1, "topologyKey": "topology.kubernetes.io/zone",
                                             "whenUnsatisfiable": "DoNotSchedule",
                                             "labelSelector": {"matchLabels": {"app": name}}}]
    return {"apiVersion": "apps/v1", "kind": "Deployment",
            "metadata": {"name": name, "namespace": NS, "annotations": {"deployment.kubernetes.io/revision": "3"}},
            "spec": {"replicas": replicas, "selector": {"matchLabels": {"app": name}},
                     "strategy": {"type": "RollingUpdate", "rollingUpdate": {"maxSurge": "25%", "maxUnavailable": "25%"}},
                     "template": {"metadata": {"labels": {"app": name}, "annotations": {"redeploy-timestamp": "1"}},
                                  "spec": pod}}}


def service(base):
    return {"apiVersion": "v1", "kind": "Service", "metadata": {"name": f"{base}-service", "namespace": NS,
                                                                "labels": {"app": f"{base}-app"}},
            "spec": {"type": "NodePort", "selector": {"app": f"{base}-app"}, "ports": [
                {"name": "http", "port": 8080, "targetPort": 8080, "nodePort": 30080, "protocol": "TCP"},
                {"name": "https", "port": 8443, "targetPort": 8443, "nodePort": 30443, "protocol": "TCP"}]}}


def ingress(base, certificate, inconsistent=False):
    annotations = {"kubernetes.io/elb.class": "performance", "kubernetes.io/elb.id": "elb-shared",
                   "kubernetes.io/elb.port": "443", "kubernetes.io/elb.tls-certificate-ids": certificate}
    if inconsistent:
        annotations["kubernetes.io/elb.inconsistent-listener-config"] = "certificate"
    return {"apiVersion": "networking.k8s.io/v1", "kind": "Ingress",
            "metadata": {"name": f"{base}-ingress", "namespace": NS, "annotations": annotations},
            "spec": {"ingressClassName": "cce", "rules": [{"host": f"{base}-app.{NS}", "http": {"paths": [{
                "path": "/", "pathType": "Prefix", "property": {"ingress.beta.kubernetes.io/url-match-mode": "STARTS_WITH"},
                "backend": {"service": {"name": f"{base}-service", "port": {"number": 8080}}}}]}}]}}


def configmap(name, data):
    return {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": name, "namespace": NS}, "data": data}


def secret(name, data, kind="Opaque"):
    return {"apiVersion": "v1", "kind": "Secret", "type": kind, "metadata": {"name": name, "namespace": NS},
            "data": {k: b64(v) for k, v in data.items()}}


def export():
    objects = [
        deployment("order-api"), deployment("billing-api", container="billing", scheme="HTTPS", affinity=False),
        deployment("stock-api"),
        dict(deployment("report-api"), spec=dict(deployment("report-api")["spec"], template={
            "metadata": {"labels": {"app": "report-api-app"}},
            "spec": {"containers": [{"name": "a", "image": "swr.example/team/report:1"}, {"name": "b", "image": "x"}]}})),
        service("order-api"), service("billing-api"), service("stock-api"),
        ingress("order-api", "cert-a"), ingress("billing-api", "cert-b", inconsistent=True), ingress("stock-api", "cert-a"),
        configmap("order-api-configmap", {"SHARED_FLAG": "on", "REGION_CODE": "r1", "OWN_KEY": "o1"}),
        configmap("billing-api-configmap", {"SHARED_FLAG": "on", "REGION_CODE": "r1", "OWN_KEY": "b1"}),
        configmap("stock-api-configmap", {"SHARED_FLAG": "on", "REGION_CODE": "r1", "OWN_KEY": "s1"}),
        configmap("shared-config", {"app.properties": "timeout=30\n", "global.properties": "zone=uat\n"}),
        configmap("order-api-properties", {"custom.properties": "feature=a\n"}),
        configmap("billing-api-properties", {"custom.properties": "feature=b\n"}),
        configmap("stock-api-properties", {"custom.properties": "feature=s\n"}),
        configmap("unused-config", {"x": "1"}), configmap("kube-root-ca.crt", {"ca.crt": "x"}),
        secret("order-api-secrets", {"DB_PASSWORD": "common-pass", "API_TOKEN": "tok-o"}),
        secret("billing-api-secrets", {"DB_PASSWORD": "common-pass", "API_TOKEN": "tok-b"}),
        secret("stock-api-secrets", {"DB_PASSWORD": "common-pass", "API_TOKEN": "tok-s"}),
        secret("trust-store", {"ca.pem": "trusted-ca", "store.jks": JKS}),
        secret("pull-secret", {".dockerconfigjson": "{}"}, "kubernetes.io/dockerconfigjson"),
        secret("elb-credential", {"k": "v"}, "cfe/secure-opaque"),
        {"apiVersion": "autoscaling/v2", "kind": "HorizontalPodAutoscaler", "metadata": {"name": "billing-hpa", "namespace": NS},
         "spec": {"scaleTargetRef": {"apiVersion": "apps/v1", "kind": "Deployment", "name": "billing-api-app"},
                  "minReplicas": 1, "maxReplicas": 3,
                  "metrics": [{"type": "Resource", "resource": {"name": "cpu",
                                                                "target": {"type": "Utilization", "averageUtilization": 65}}}]}},
        {"apiVersion": "apps/v1", "kind": "ReplicaSet", "metadata": {"name": "order-api-app-1", "namespace": NS}},
        {"apiVersion": "v1", "kind": "ServiceAccount", "metadata": {"name": "default", "namespace": NS}},
    ]
    for base, container in (("order-api", "order-api-app"), ("billing-api", "billing")):
        objects.append({"apiVersion": "v1", "kind": "Pod", "metadata": {"name": f"{base}-app-x", "namespace": NS,
                                                                       "labels": {"app": f"{base}-app"}},
                        "status": {"containerStatuses": [{"name": container, "imageID":
                                   f"swr.example/team/{base}@sha256:" + ("a" if base == "order-api" else "b") * 64}]}})
    return objects


def to_yaml(objects):
    return yaml.safe_dump({"apiVersion": "v1", "kind": "List", "items": objects}, allow_unicode=True)


@pytest.fixture
def imported(tmp_path):
    settings = Settings()
    settings.build.data_dir = str(tmp_path / "data")
    settings.server.admin_password = "AdminPass123"
    settings.registry.address = "swr.example"
    kube = FakeKube([NS])
    app = create_app(settings, deploy_transport=httpx.MockTransport(kube))
    with TestClient(app, headers=HEAD) as client:
        login(client)
        project = client.post("/api/projects", json={"slug": "orders", "name": "订单",
                                                    "registry_orgs": [{"namespace": "team", "base_prefixes": []}]}).json()
        cluster = client.post("/api/clusters", json={"name": "cce-b", "server": "https://kube.example",
                                                    "auth_type": "sa_token", "credential": {"token": "cluster-token"}}).json()
        namespace = client.post("/api/namespaces", json={"name": NS, "cluster_id": cluster["id"], "tier": "test",
                                                        "project_id": project["id"], "policy": {"require_approval": False}})
        assert namespace.status_code == 201, namespace.text
        yield {"app": app, "client": client, "kube": kube, "project": project, "namespace": namespace.json()}


def test_parse_documents_rejects_aliases_and_flattens_lists():
    assert len(parse_documents(to_yaml(export()))) == len(export())
    assert [o["kind"] for o in parse_documents("kind: ConfigMap\nmetadata: {name: a}\n---\n---\nkind: Secret\n")] == \
        ["ConfigMap", "Secret"]
    with pytest.raises(ValueError, match="别名"):
        parse_documents("a: &x [1]\nb: *x\n")
    with pytest.raises(ValueError, match="Kubernetes 对象"):
        parse_documents("- 1\n- 2\n")


def test_analyze_proposes_layers_and_flags_inconsistencies_without_secret_values(imported):
    client, namespace, project = imported["client"], imported["namespace"], imported["project"]
    url = f"/api/environments/{namespace['id']}/import/analyze"
    blocked = client.post(url, json={"source": "yaml", "yaml": to_yaml(export()), "project_id": project["id"]})
    assert blocked.status_code == 200, blocked.text
    assert any("NodePort" in b for b in blocked.json()["blockers"]) and any("Ingress" in b for b in blocked.json()["blockers"])
    client.put(f"/api/namespaces/{namespace['id']}", json={"tier": "test", "policy": OPEN_POLICY})
    response = client.post(url, json={"source": "yaml", "yaml": to_yaml(export()), "project_id": project["id"]})
    report = response.json()
    for value in ("common-pass", "tok-o", "tok-b", "trusted-ca", b64(JKS)):
        assert value not in response.text
    assert report["blockers"] == []
    services = {s["deployment"]: s for s in report["services"]}
    assert services["report-api-app"]["action"] == "unsupported" and "单容器" in services["report-api-app"]["reason"]
    order, billing = services["order-api-app"], services["billing-api-app"]
    assert order["action"] == billing["action"] == "create" and order["name"] == "order-api"
    assert order["app"] == {"create": {"repository": "order-api", "organization": "team"}}
    assert order["digest"] == "sha256:" + "a" * 64 and order["spec"]["names"] == {}
    assert billing["spec"]["names"] == {"hpa": "billing-hpa"} and billing["spec"]["container"]["name"] == "billing"
    assert billing["spec"]["probe"]["scheme"] == "HTTPS" and billing["spec"]["hpa"]["max_replicas"] == 3
    assert order["spec"]["mounts"] == [
        {"config": "trust-store", "path": "/opt/trust", "items": [], "volume": "trust"},
        {"config": "shared-config", "path": "/app/config", "items": ["app.properties", "global.properties"], "volume": "shared"},
        {"config": "properties", "path": "/app/config", "items": ["custom.properties"], "volume": "own"}]
    assert order["spec"]["service"]["ports"][0] == {"name": "http", "port": 8080, "target_port": 8080, "node_port": 30080}
    assert order["config"] == {"env": ["OWN_KEY"], "secret_env": ["API_TOKEN"], "files": ["custom.properties"]}
    assert order["effective"]["env"] == {"added": [], "changed": [], "locked": []}
    assert any("节点池亲和" in w for w in billing["warnings"])
    profile = report["profile"]["proposed"]
    assert profile["naming"]["deployment"] == "{base}-app" and profile["host"] == "{base}-app.{namespace}"
    assert profile["scheduling"]["node_pool"] == "pool-a" and profile["image_pull_secret"] == "pull-secret"
    assert profile["log"] == {"enabled": True, "host_path": "/var/log/apps", "mount_path": "/var/log/app",
                              "sub_path_expr": "$(POD_NAMESPACE)/$(POD_NAME)"}
    assert profile["defaults"] == {"replicas": 2, "cpu_request": "500m", "memory_request": "1Gi", "cpu_limit": "1",
                                   "memory_limit": "1Gi"}
    assert report["ingress"]["proposed"]["certificate_ids"] == ["cert-a"]
    assert report["ingress"]["proposed"]["url_match_mode"] == "STARTS_WITH"
    assert report["env"]["keys"] == {"REGION_CODE": "r1", "SHARED_FLAG": "on"}
    assert report["secret_env"]["keys"] == ["DB_PASSWORD"]
    assert {(f["name"], f["kind"]) for f in report["files"]} == {("shared-config", "files"), ("trust-store", "secret_files")}
    warnings = "\n".join(report["warnings"])
    assert "unused-config" in warnings
    assert "billing-api-app 的 Ingress 与多数服务的证书 ID不同" in warnings
    assert "CCE 已标记 billing-api-app 的 Ingress 与共享监听器配置不一致" in warnings
    assert report["ignored"] == {"ReplicaSet": 1, "ServiceAccount": 1}


def test_import_apply_then_adopt_takes_over_live_objects(imported):
    client, kube, namespace, project = imported["client"], imported["kube"], imported["namespace"], imported["project"]
    client.put(f"/api/namespaces/{namespace['id']}", json={"tier": "test", "policy": OPEN_POLICY})
    document = to_yaml(export())
    applied = client.post(f"/api/environments/{namespace['id']}/import/apply", json={
        "source": "yaml", "yaml": document, "project_id": project["id"], "save_profile": True, "ingress": "create",
        "save_config": True, "use_certificates": False,
        "services": [{"deployment": "order-api-app", "create_app": True},
                     {"deployment": "billing-api-app", "create_app": True}, {"deployment": "report-api-app"}]})
    assert applied.status_code == 200, applied.text
    result = applied.json()
    assert result["errors"] == [] and result["profile"]["version"] == 1 and result["ingress_profile"]
    assert [s["status"] for s in result["services"]] == ["created", "created", "error"], result["services"]
    assert {s["name"] for s in result["config_sets"]} == {"env", "secret-env", "shared-config", "trust-store"}
    assert len(result["apps"]) == 2
    services = {s["deployment"]: s for s in result["services"]}

    # 集群中的现状与导出一致；接管发布沿用线上镜像
    for obj in export():
        if obj["kind"] not in ("ReplicaSet", "ServiceAccount", "Pod"):
            kube.put(copy.deepcopy(obj))
    for deployment_name, digest in (("order-api-app", "e" * 64), ("billing-api-app", "e" * 64)):
        service_id = services[deployment_name]["service_id"]
        response = client.post(f"/api/services/{service_id}/adopt", json={}, headers={"Idempotency-Key": f"adopt-service-{service_id}"})
        assert response.status_code == 202, response.text
        for _ in range(300):
            record = client.get(f"/api/deployments/{response.json()['id']}").json()
            if record["status"] in ("success", "failed", "unknown"):
                break
            time.sleep(0.03)
        assert record["status"] == "success", record
        # 接管沿用 Deployment 中的镜像地址（版本号），digest 取自正在运行的 Pod
        base = deployment_name.removesuffix("-app")
        assert record["job_id"] is None and record["image"] == f"swr.example/team/{base}:2.1"
        assert record["digest"] == "sha256:" + digest
    assert kube.get("configmaps", "order-api-configmap", NS)["data"] == {"SHARED_FLAG": "on", "REGION_CODE": "r1",
                                                                          "OWN_KEY": "o1"}
    billing_secret = kube.get("secrets", "billing-api-secrets", NS)["data"]
    assert base64.b64decode(billing_secret["API_TOKEN"]) == b"tok-b" and base64.b64decode(billing_secret["DB_PASSWORD"]) == \
        b"common-pass"
    assert base64.b64decode(kube.get("secrets", "trust-store", NS)["data"]["store.jks"]) == JKS
    assert kube.get("configmaps", "billing-api-properties", NS)["data"] == {"custom.properties": "feature=b\n"}
    # 证书 ID 统一为环境的负载均衡入口，修正了线上不一致的 Ingress；HPA 沿用原名称，没有新建第二个
    certificates = {kube.get("ingresses", f"{b}-ingress", NS)["metadata"]["annotations"]["kubernetes.io/elb.tls-certificate-ids"]
                    for b in ("order-api", "billing-api")}
    assert len(certificates) == 1
    assert [k for k in kube.objects if k[0] == "horizontalpodautoscalers"] == [("horizontalpodautoscalers", NS, "billing-hpa")]
    billing = kube.get("deployments", "billing-api-app", NS)
    assert billing["spec"]["template"]["spec"]["containers"][0]["name"] == "billing"
    assert billing["spec"]["template"]["spec"]["affinity"]["nodeAffinity"]  # 按环境档案补齐调度规则
    again = client.post(f"/api/environments/{namespace['id']}/import/analyze",
                        json={"source": "yaml", "yaml": document, "project_id": project["id"]}).json()
    assert {s["deployment"]: s["action"] for s in again["services"]} == {
        "billing-api-app": "skip", "order-api-app": "skip", "stock-api-app": "create", "report-api-app": "unsupported"}


def test_import_without_ingress_permission_keeps_live_ingress_unmanaged(imported):
    from swr_builder.security import hash_password

    client, kube, namespace, project = imported["client"], imported["kube"], imported["namespace"], imported["project"]
    client.put(f"/api/namespaces/{namespace['id']}", json={"tier": "test", "policy": OPEN_POLICY})
    store = imported["app"].state.store
    owner = store.create_user(username="importer", password_hash=hash_password("UserPass123"), role="member", enabled=1,
                              must_change_password=0)
    store.create_binding("user", owner["id"], "namespace_owner", "namespace", namespace["id"], None)
    store.create_binding("user", owner["id"], "project_admin", "project", project["id"], None)
    importer = TestClient(imported["app"], headers=HEAD)
    assert login(importer, "importer", "UserPass123").status_code == 200
    document = to_yaml(export())
    report = importer.post(f"/api/environments/{namespace['id']}/import/analyze",
                           json={"source": "yaml", "yaml": document, "project_id": project["id"]}).json()
    assert not report["permissions"]["certificate_manage"] and "Ingress 暂不由平台托管" in report["warnings"][0]
    result = importer.post(f"/api/environments/{namespace['id']}/import/apply", json={
        "source": "yaml", "yaml": document, "project_id": project["id"], "save_profile": True, "ingress": "create",
        "save_config": True, "use_certificates": True, "services": [{"deployment": "order-api-app", "create_app": True}]}).json()
    assert result["ingress_profile"] is None and any("certificate.manage" in e for e in result["errors"])
    assert any("暂不托管 Ingress" in n for n in result["notes"])
    service_id = result["services"][0]["service_id"]
    assert client.get(f"/api/services/{service_id}").json()["spec"]["ingress"]["enabled"] is False
    for obj in export():
        if obj["kind"] not in ("ReplicaSet", "ServiceAccount", "Pod"):
            kube.put(copy.deepcopy(obj))
    response = importer.post(f"/api/services/{service_id}/adopt", json={}, headers={"Idempotency-Key": "adopt-without-ingress"})
    assert response.status_code == 202, response.text
    for _ in range(300):
        record = client.get(f"/api/deployments/{response.json()['id']}").json()
        if record["status"] in ("success", "failed", "unknown"):
            break
        time.sleep(0.03)
    assert record["status"] == "success", record
    live = kube.get("ingresses", "order-api-ingress", NS)
    assert live and live["metadata"]["annotations"]["kubernetes.io/elb.tls-certificate-ids"] == "cert-a"
