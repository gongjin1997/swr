"""Read-only Kubernetes discovery, stripped of credentials and pod environment values."""
from urllib.parse import quote

from ..deploy.k8s_client import KubeClient
from ..errors import DomainError
from ..store import utcnow
from .clusters import load_cluster


async def discover_cluster(ctx, cluster_id):
    cluster, credential = load_cluster(ctx.store, ctx.box, cluster_id)
    warnings = []
    async with KubeClient(cluster["server"], credential, ctx.deploy_transport) as kube:
        version = await kube.version()
        rows = await kube.list_resources("/api/v1/namespaces")
        nodes = []
        try:
            nodes = await kube.list_resources("/api/v1/nodes")
        except DomainError as exc:
            warnings.append(str(exc))
    managed = {n["name"]: n for n in ctx.store.list_namespaces(cluster_id=cluster_id)}
    namespaces = [{"name": n.get("metadata", {}).get("name", ""),
                   "status": n.get("status", {}).get("phase", "Unknown"),
                   "managed_id": managed.get(n.get("metadata", {}).get("name"), {}).get("id")}
                  for n in rows]
    nodes = [{"name": n.get("metadata", {}).get("name", ""),
              "version": n.get("status", {}).get("nodeInfo", {}).get("kubeletVersion", ""),
              "ready": any(c.get("type") == "Ready" and c.get("status") == "True"
                           for c in n.get("status", {}).get("conditions", [])),
              "architecture": n.get("status", {}).get("nodeInfo", {}).get("architecture", "")}
             for n in nodes]
    ctx.store.set_cluster_status(cluster_id, "ready")
    return {"cluster_id": cluster_id, "version": version, "namespaces": namespaces, "nodes": nodes,
            "warnings": warnings, "fetched_at": utcnow()}


async def namespace_resources(ctx, namespace):
    cluster, credential = load_cluster(ctx.store, ctx.box, namespace["cluster_id"])
    name = quote(namespace["name"], safe="")
    paths = {"deployments": f"/apis/apps/v1/namespaces/{name}/deployments",
             "services": f"/api/v1/namespaces/{name}/services", "pods": f"/api/v1/namespaces/{name}/pods",
             "ingresses": f"/apis/networking.k8s.io/v1/namespaces/{name}/ingresses"}
    data, warnings = {}, []
    async with KubeClient(cluster["server"], credential, ctx.deploy_transport) as kube:
        for key, path in paths.items():
            try:
                data[key] = await kube.list_resources(path)
            except DomainError as exc:
                if key == "deployments":
                    raise
                data[key] = []
                warnings.append(str(exc))
    deployments = []
    for item in data["deployments"]:
        spec, status = item.get("spec", {}), item.get("status", {})
        template = spec.get("template", {})
        deployments.append({"name": item.get("metadata", {}).get("name", ""),
                            "uid": item.get("metadata", {}).get("uid", ""),
                            "replicas": spec.get("replicas", 1), "ready_replicas": status.get("readyReplicas", 0),
                            "labels": template.get("metadata", {}).get("labels", {}),
                            "containers": [{"name": c.get("name", ""), "image": c.get("image", "")}
                                           for c in template.get("spec", {}).get("containers", [])]})
    services = [{"name": s.get("metadata", {}).get("name", ""), "type": s.get("spec", {}).get("type", ""),
                 "cluster_ip": s.get("spec", {}).get("clusterIP", ""),
                 "selector": s.get("spec", {}).get("selector", {}), "ports": s.get("spec", {}).get("ports", [])}
                for s in data["services"]]
    pods = [{"name": p.get("metadata", {}).get("name", ""), "phase": p.get("status", {}).get("phase", ""),
             "labels": p.get("metadata", {}).get("labels", {}), "node": p.get("spec", {}).get("nodeName", ""),
             "ready": any(c.get("type") == "Ready" and c.get("status") == "True"
                          for c in p.get("status", {}).get("conditions", []))} for p in data["pods"]]
    ingresses = [{"name": i.get("metadata", {}).get("name", ""),
                  "hosts": [r.get("host", "") for r in i.get("spec", {}).get("rules", [])],
                  "service_names": sorted({p.get("backend", {}).get("service", {}).get("name", "")
                                           for r in i.get("spec", {}).get("rules", [])
                                           for p in r.get("http", {}).get("paths", [])})}
                 for i in data["ingresses"]]
    return {"namespace_id": namespace["id"], "cluster_name": namespace["cluster_name"],
            "namespace_name": namespace["name"], "deployments": deployments, "services": services,
            "pods": pods, "ingresses": ingresses, "warnings": warnings, "fetched_at": utcnow()}


async def service_runtime(ctx, service):
    ns = ctx.store.get_namespace(service["namespace_id"])
    resources = await namespace_resources(ctx, ns)
    target = (service.get("spec") or {}).get("target", {})
    workload = next((d for d in resources["deployments"] if d["name"] == target.get("name")), None)
    labels = workload["labels"] if workload else {}
    services = [s for s in resources["services"] if s["selector"] and
                all(labels.get(k) == v for k, v in s["selector"].items())] if workload else []
    service_names = {s["name"] for s in services}
    pods = [p for p in resources["pods"] if labels and all(p["labels"].get(k) == v for k, v in labels.items())]
    ingresses = [i for i in resources["ingresses"] if service_names.intersection(i["service_names"])]
    return dict(resources, deployments=[workload] if workload else [], services=services, pods=pods,
                ingresses=ingresses, found=bool(workload),
                container=next((c for c in (workload or {}).get("containers", []) if c["name"] == target.get("container")), None))
