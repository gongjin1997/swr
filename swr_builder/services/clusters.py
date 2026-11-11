"""集群纳管：kubeconfig 解析、凭据加解密、连接测试、新建/修改/轮换凭据。"""
from __future__ import annotations

import json
import ssl
from typing import Any, Dict, List, Optional, Tuple

from ..deploy.k8s_client import KubeClient, build_ssl_context, validate_api_server
from ..deploy.kubeconfig import parse_kubeconfig
from ..errors import DomainError, NotFound, NotImplementedYet
from ..security import SecretBox
from ..store import Store
from ..validation import reject_unknown, text_field

CLUSTER_FIELDS = {"name", "server", "auth_type", "credential", "config", "cce_cluster_id", "cert_duration_days"}
PUBLIC_FIELDS = ("id", "name", "auth_type", "server", "cce_cluster_id", "credential_expires_at",
                 "cert_duration_days", "status", "created_at", "updated_at")
SUPPORTED_AUTH = ("sa_token", "client_cert")
UNSUPPORTED_AUTH = "当前仅支持 kubeconfig 静态 Token 和客户端证书认证"
MAX_PEM = 1024 * 1024


def public_cluster(cluster: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not cluster:
        raise NotFound("集群不存在")
    return {key: cluster.get(key) for key in PUBLIC_FIELDS}


def normalize_credential(data: Any, auth_type: Optional[str] = None) -> Dict[str, Any]:
    allowed = {"token", "ca_pem", "client_cert_pem", "client_key_pem", "insecure_skip_tls_verify"}
    if not isinstance(data, dict) or set(data) - allowed:
        raise ValueError("集群凭据格式无效")
    token, cert, key = data.get("token", ""), data.get("client_cert_pem", ""), data.get("client_key_pem", "")
    ca_pem, insecure = data.get("ca_pem", ""), data.get("insecure_skip_tls_verify", False)
    if not isinstance(ca_pem, str) or len(ca_pem) > MAX_PEM or type(insecure) is not bool:
        raise ValueError("CA 证书或 TLS 配置无效")
    if cert or key:
        if (token or not isinstance(cert, str) or not isinstance(key, str) or not cert or not key
                or len(cert) > MAX_PEM or len(key) > MAX_PEM):
            raise ValueError("客户端证书和私钥必须成对提供，且不能与 Token 混用")
        selected_type = "client_cert"
        credential = {"client_cert_pem": cert, "client_key_pem": key,
                      "ca_pem": ca_pem, "insecure_skip_tls_verify": insecure}
    elif isinstance(token, str) and token and len(token) <= 65536 and "\r" not in token and "\n" not in token:
        selected_type = "sa_token"
        credential = {"token": token, "ca_pem": ca_pem, "insecure_skip_tls_verify": insecure}
    else:
        raise ValueError("请提供静态 SA Token 或内嵌客户端证书/私钥")
    if auth_type is not None and auth_type != selected_type:
        raise ValueError("认证类型与凭据不匹配")
    try:
        build_ssl_context(credential)
    except (ssl.SSLError, OSError, UnicodeError, ValueError):
        raise ValueError("集群 CA 或客户端证书/私钥无效") from None
    return credential


def encrypt_credential(box: SecretBox, credential: Dict[str, Any]) -> str:
    return box.encrypt(json.dumps(credential, ensure_ascii=False))


def normalize_cluster_input(data: Dict[str, Any], existing: Optional[Dict[str, Any]] = None):
    """校验配置并返回 (name, server, auth_type, duration, credential)；修改时未提供凭据则 credential 为 None。"""
    if not isinstance(data, dict):
        raise ValueError("集群请求格式错误")
    reject_unknown(data, CLUSTER_FIELDS, "集群请求包含未知字段")
    name = text_field(data, "name", 80)
    if not name:
        raise ValueError("请填写集群名称")
    config = data.get("config")
    if config is not None and not isinstance(config, str):
        raise ValueError("kubeconfig 必须是 YAML 文本")
    if config:
        if any(key in data for key in ("server", "auth_type", "credential")):
            raise ValueError("kubeconfig 不能与单独的 API Server/凭据字段混用")
        server, auth_type, raw_credential = parse_kubeconfig(config)
        credential: Optional[Dict[str, Any]] = normalize_credential(raw_credential, auth_type)
    else:
        if not existing and "config" in data:
            raise ValueError("kubeconfig 不能为空")
        server = text_field(data, "server", 2048) if "server" in data else (existing or {}).get("server", "")
        auth_type = data.get("auth_type", (existing or {}).get("auth_type"))
        if auth_type not in SUPPORTED_AUTH:
            raise NotImplementedYet(UNSUPPORTED_AUTH)
        if existing and "credential" not in data:
            if auth_type != existing["auth_type"]:
                raise ValueError("更换集群认证方式时必须同时提供新凭据")
            credential = None
        else:
            credential = normalize_credential(data.get("credential"), auth_type)
        try:
            validate_api_server(server)
        except (DomainError, ValueError):
            raise ValueError("API Server 必须是不含凭据的 HTTPS 地址") from None
    duration = data.get("cert_duration_days", existing["cert_duration_days"] if existing else 30)
    if type(duration) is not int or not 1 <= duration <= 90:
        raise ValueError("凭据有效期范围为 1–90 天")
    return name, server.rstrip("/"), auth_type, duration, credential


def load_cluster(store: Store, box: SecretBox, cluster_id: int) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """读取集群记录并解密凭据。"""
    cluster = store.get_cluster_record(cluster_id)
    if not cluster:
        raise NotFound("集群不存在")
    if cluster["auth_type"] not in SUPPORTED_AUTH:
        raise NotImplementedYet(UNSUPPORTED_AUTH)
    try:
        credential = json.loads(box.decrypt(cluster["credential_encrypted"]))
        credential = normalize_credential(credential, cluster["auth_type"])
    except (ValueError, TypeError):
        raise DomainError("集群凭据无法解密", status=500, code="CREDENTIAL_DECRYPT_FAILED") from None
    return cluster, credential


async def test_connection(cluster: Dict[str, Any], credential: Dict[str, Any], transport: Any = None) -> str:
    """连接集群并返回 Kubernetes 版本。"""
    validate_api_server(cluster["server"])
    async with KubeClient(cluster["server"], credential, transport) as kube:
        return await kube.version()


# ------------------------------------------------------------------ 集群生命周期
def list_clusters(store: Store) -> List[Dict[str, Any]]:
    return [public_cluster(row) for row in store.list_clusters()]


def create_cluster(store: Store, box: SecretBox, data: Dict[str, Any]) -> Dict[str, Any]:
    name, server, auth_type, duration, credential = normalize_cluster_input(data)
    cluster_id = store.create_cluster({
        "name": name, "server": server, "auth_type": auth_type,
        "credential_encrypted": encrypt_credential(box, credential),  # type: ignore[arg-type]
        "cce_cluster_id": text_field(data, "cce_cluster_id", 128) or None, "cert_duration_days": duration})
    return public_cluster(store.get_cluster(cluster_id))


def update_cluster(store: Store, box: SecretBox, cluster_id: int, data: Dict[str, Any]) -> Dict[str, Any]:
    """修改名称、地址或凭据；修改后状态回到"未验证"，需要重新测试连接。"""
    existing = store.get_cluster_record(cluster_id)
    if not existing:
        raise NotFound("集群不存在")
    name, server, auth_type, duration, credential = normalize_cluster_input(data, existing)
    encrypted = existing["credential_encrypted"] if credential is None else encrypt_credential(box, credential)
    store.update_cluster(cluster_id, {
        "name": name, "server": server, "auth_type": auth_type, "credential_encrypted": encrypted,
        "cce_cluster_id": text_field(data, "cce_cluster_id", 128) or existing["cce_cluster_id"],
        "credential_expires_at": existing["credential_expires_at"], "cert_duration_days": duration,
        "status": "unverified"})
    return public_cluster(store.get_cluster(cluster_id))


async def verify_cluster(store: Store, box: SecretBox, cluster_id: int, transport: Any = None) -> str:
    """测试连接，成功后标记为可用；返回 Kubernetes 版本。"""
    cluster, credential = load_cluster(store, box, cluster_id)
    version = await test_connection(cluster, credential, transport)
    store.set_cluster_status(cluster_id, "ready")
    return version


async def rotate_credential(store: Store, box: SecretBox, cluster_id: int, data: Any, transport: Any = None) -> str:
    """先用新凭据连通集群，再替换保存的凭据；返回 Kubernetes 版本。"""
    if not isinstance(data, dict) or set(data) != {"credential"}:
        raise ValueError("请提供新凭据")
    cluster = store.get_cluster_record(cluster_id)
    if not cluster:
        raise NotFound("集群不存在")
    credential = normalize_credential(data["credential"], cluster["auth_type"])
    version = await test_connection(cluster, credential, transport)
    store.update_cluster(cluster_id, {**cluster, "credential_encrypted": encrypt_credential(box, credential),
                                      "status": "ready"})
    return version


def revoke_credential(store: Store, cluster_id: int) -> None:
    cluster = store.get_cluster(cluster_id)
    if not cluster:
        raise NotFound("集群不存在")
    if cluster["auth_type"] != "cce_cert":
        raise DomainError("只有 CCE 签发证书支持撤销")
    raise NotImplementedYet("CCE 证书撤销尚未接入现场 CCE API")
