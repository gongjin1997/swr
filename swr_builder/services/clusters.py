"""集群纳管：字段校验、凭据加解密、连接测试。"""
from __future__ import annotations

import json
import ssl
from typing import Any, Dict, Optional, Tuple

from ..deploy.k8s_client import KubeClient, validate_api_server
from ..errors import DomainError, NotFound, NotImplementedYet
from ..security import SecretBox
from ..store import Store
from ..validation import reject_unknown, text_field

CLUSTER_FIELDS = {"name", "server", "auth_type", "credential", "cce_cluster_id", "cert_duration_days"}
PUBLIC_FIELDS = ("id", "name", "auth_type", "server", "cce_cluster_id", "credential_expires_at",
                 "cert_duration_days", "status")
SA_TOKEN_ONLY = "当前版本先实现 SA Token 接入；证书认证需要现场适配"


def public_cluster(cluster: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not cluster:
        raise NotFound("集群不存在")
    return {key: cluster.get(key) for key in PUBLIC_FIELDS}


def normalize_credential(data: Any) -> Dict[str, str]:
    if not isinstance(data, dict) or set(data) - {"token", "ca_pem"}:
        raise ValueError("当前仅支持 SA Token 凭据（token、ca_pem）")
    token, ca_pem = data.get("token"), data.get("ca_pem", "")
    if not isinstance(token, str) or not token or len(token) > 65536:
        raise ValueError("SA Token 无效")
    if not isinstance(ca_pem, str) or len(ca_pem) > 1024 * 1024:
        raise ValueError("CA 证书无效")
    if ca_pem:
        try:
            ssl.create_default_context(cadata=ca_pem)
        except ssl.SSLError:
            raise ValueError("CA 证书 PEM 格式错误") from None
    return {"token": token, "ca_pem": ca_pem}


def encrypt_credential(box: SecretBox, credential: Dict[str, str]) -> str:
    return box.encrypt(json.dumps(credential, ensure_ascii=False))


def cluster_fields(data: Dict[str, Any], existing: Optional[Dict[str, Any]] = None) -> Tuple[str, str, str, int]:
    """校验集群请求体，返回 (name, server, auth_type, cert_duration_days)。"""
    reject_unknown(data, CLUSTER_FIELDS, "集群请求包含未知字段")
    name, server = text_field(data, "name", 80), text_field(data, "server", 2048)
    try:
        validate_api_server(server)
        valid_server = True
    except DomainError:
        valid_server = False
    if not name or not valid_server:
        raise ValueError("集群名称无效或 API Server 不是安全 HTTPS 地址")
    auth_type = data.get("auth_type", existing["auth_type"]) if existing else data.get("auth_type")
    if auth_type != "sa_token":
        raise NotImplementedYet(SA_TOKEN_ONLY)
    duration = data.get("cert_duration_days", existing["cert_duration_days"] if existing else 30)
    if type(duration) is not int or not 1 <= duration <= 90:
        raise ValueError("凭据有效期范围为 1–90 天")
    return name, server.rstrip("/"), auth_type, duration


def load_cluster(store: Store, box: SecretBox, cluster_id: int) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """读取集群记录并解密凭据。"""
    cluster = store.get_cluster_record(cluster_id)
    if not cluster:
        raise NotFound("集群不存在")
    if cluster["auth_type"] != "sa_token":
        raise NotImplementedYet("当前版本仅实现 SA Token 接入，证书认证待现场适配")
    try:
        credential = json.loads(box.decrypt(cluster["credential_encrypted"]))
    except (ValueError, TypeError):
        raise DomainError("集群凭据无法解密", status=500, code="CREDENTIAL_DECRYPT_FAILED") from None
    return cluster, credential


async def test_connection(cluster: Dict[str, Any], credential: Dict[str, Any], transport: Any = None) -> str:
    """连接集群并返回 Kubernetes 版本。"""
    validate_api_server(cluster["server"])
    async with KubeClient(cluster["server"], credential, transport) as kube:
        return await kube.version()
