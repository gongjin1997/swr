"""集群纳管：kubeconfig 解析、凭据加解密、连接测试。"""
from __future__ import annotations

import json
import ssl
from typing import Any, Dict, Optional, Tuple

from ..deploy.k8s_client import KubeClient, build_ssl_context, validate_api_server
from ..deploy.kubeconfig import parse_kubeconfig
from ..errors import DomainError, NotFound, NotImplementedYet
from ..security import SecretBox
from ..store import Store
from ..validation import reject_unknown, text_field

CLUSTER_FIELDS = {"name", "server", "auth_type", "credential", "config", "cce_cluster_id", "cert_duration_days"}
PUBLIC_FIELDS = ("id", "name", "auth_type", "server", "cce_cluster_id", "credential_expires_at",
                 "cert_duration_days", "status")
UNSUPPORTED_AUTH = "当前仅支持 kubeconfig 静态 Token 和客户端证书认证"


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
    if not isinstance(ca_pem, str) or len(ca_pem) > 1024 * 1024 or type(insecure) is not bool:
        raise ValueError("CA 证书或 TLS 配置无效")
    if cert or key:
        if (token or not isinstance(cert, str) or not isinstance(key, str) or not cert or not key
                or len(cert) > 1024 * 1024 or len(key) > 1024 * 1024):
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
    """校验配置并返回 name/server/auth_type/duration/credential。"""
    reject_unknown(data, CLUSTER_FIELDS, "集群请求包含未知字段")
    name = text_field(data, "name", 80)
    if not name:
        raise ValueError("集群名称无效")
    config = data.get("config")
    if config is not None and not isinstance(config, str):
        raise ValueError("kubeconfig 必须是 YAML 文本")
    if config:
        if any(key in data for key in ("server", "auth_type", "credential")):
            raise ValueError("kubeconfig 不能与单独的 API Server/凭据字段混用")
        server, auth_type, raw_credential = parse_kubeconfig(config)
        credential = normalize_credential(raw_credential, auth_type)
    else:
        if not existing and "config" in data:
            raise ValueError("kubeconfig 不能为空")
        server = text_field(data, "server", 2048) if "server" in data else (existing or {}).get("server", "")
        auth_type = data.get("auth_type", (existing or {}).get("auth_type"))
        if auth_type not in ("sa_token", "client_cert"):
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
            raise ValueError("集群名称无效或 API Server 不是安全 HTTPS 地址") from None
    duration = data.get("cert_duration_days", existing["cert_duration_days"] if existing else 30)
    if type(duration) is not int or not 1 <= duration <= 90:
        raise ValueError("凭据有效期范围为 1–90 天")
    return name, server.rstrip("/"), auth_type, duration, credential


def cluster_fields(data: Dict[str, Any], existing: Optional[Dict[str, Any]] = None) -> Tuple[str, str, str, int]:
    """兼容旧调用方的字段校验结果。"""
    return normalize_cluster_input(data, existing)[:4]


def load_cluster(store: Store, box: SecretBox, cluster_id: int) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """读取集群记录并解密凭据。"""
    cluster = store.get_cluster_record(cluster_id)
    if not cluster:
        raise NotFound("集群不存在")
    if cluster["auth_type"] not in ("sa_token", "client_cert"):
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
