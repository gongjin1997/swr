"""解析单文件标准 kubeconfig；不执行 exec 插件或读取配置外部的凭据文件。"""
from __future__ import annotations

import base64

import yaml
from yaml.events import AliasEvent

from ..errors import DomainError
from .k8s_client import validate_api_server

MAX_CONFIG_BYTES = 1024 * 1024
MAX_ALIASES = 32


class _KubeconfigLoader(yaml.SafeLoader):
    def __init__(self, stream):
        super().__init__(stream)
        self._alias_count = 0

    def compose_node(self, parent, index):
        if self.check_event(AliasEvent):
            self._alias_count += 1
            if self._alias_count > MAX_ALIASES:
                raise yaml.YAMLError("too many aliases")
        return super().compose_node(parent, index)

    def construct_mapping(self, node, deep=False):
        self.flatten_mapping(node)
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicate = key in result
            except TypeError:
                raise yaml.YAMLError("mapping keys must be scalar") from None
            if duplicate:
                raise yaml.YAMLError("duplicate mapping key")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def _mapping(value, label):
    if not isinstance(value, dict):
        raise ValueError(f"kubeconfig 的 {label} 格式无效")
    return value


def _named_entries(value, label):
    if not isinstance(value, list):
        raise ValueError(f"kubeconfig 缺少 {label}")
    result = {}
    for entry in value:
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str) or not entry["name"]:
            raise ValueError(f"kubeconfig 的 {label} 项格式无效")
        if entry["name"] in result:
            raise ValueError(f"kubeconfig 的 {label} 名称重复")
        result[entry["name"]] = entry
    return result


def _decode_data(value, label, required=False):
    if value is None and not required:
        return ""
    if not isinstance(value, str) or not value:
        raise ValueError(f"kubeconfig 的 {label} 必须使用内嵌 Base64 数据")
    try:
        data = base64.b64decode("".join(value.split()), validate=True).decode("ascii")
    except (ValueError, UnicodeDecodeError):
        raise ValueError(f"kubeconfig 的 {label} Base64 格式无效") from None
    if len(data) > MAX_CONFIG_BYTES:
        raise ValueError(f"kubeconfig 的 {label} 超过大小限制")
    return data


def parse_kubeconfig(raw: str):
    """返回 (API Server, auth_type, credential)。只接受静态 token 或内嵌客户端证书。"""
    if not isinstance(raw, str) or not raw.strip() or len(raw.encode("utf-8")) > MAX_CONFIG_BYTES:
        raise ValueError("kubeconfig 为空或超过 1 MiB")
    try:
        config = yaml.load(raw, Loader=_KubeconfigLoader)
    except (yaml.YAMLError, RecursionError):
        raise ValueError("kubeconfig YAML 格式无效或包含过多别名") from None
    if not isinstance(config, dict) or config.get("kind") != "Config" or config.get("apiVersion") != "v1":
        raise ValueError("仅支持 apiVersion: v1、kind: Config 的 kubeconfig")

    current = config.get("current-context")
    contexts = _named_entries(config.get("contexts"), "contexts")
    if not isinstance(current, str) or current not in contexts:
        raise ValueError("kubeconfig current-context 未指向有效 context")
    context = _mapping(contexts[current].get("context"), "context")
    cluster_name, user_name = context.get("cluster"), context.get("user")
    if not isinstance(cluster_name, str) or not isinstance(user_name, str):
        raise ValueError("kubeconfig context 必须同时指定 cluster 和 user")

    clusters = _named_entries(config.get("clusters"), "clusters")
    users = _named_entries(config.get("users"), "users")
    if cluster_name not in clusters or user_name not in users:
        raise ValueError("kubeconfig context 引用的 cluster 或 user 不存在")
    cluster = _mapping(clusters[cluster_name].get("cluster"), "cluster")
    user = _mapping(users[user_name].get("user"), "user")
    server = cluster.get("server")
    if not isinstance(server, str) or len(server) > 2048:
        raise ValueError("kubeconfig API Server 地址无效")
    try:
        validate_api_server(server)
    except (DomainError, ValueError):
        raise ValueError("kubeconfig API Server 必须是无凭据的 HTTPS 地址") from None

    for key, label in (("certificate-authority", "certificate-authority"),
                       ("proxy-url", "proxy-url"), ("tls-server-name", "tls-server-name")):
        if cluster.get(key):
            raise ValueError(f"不支持 kubeconfig 的 {label} 文件路径或覆盖配置")
    insecure = cluster.get("insecure-skip-tls-verify", False)
    if type(insecure) is not bool:
        raise ValueError("kubeconfig insecure-skip-tls-verify 必须为布尔值")
    ca_pem = _decode_data(cluster.get("certificate-authority-data"), "certificate-authority-data")
    if insecure and ca_pem:
        raise ValueError("kubeconfig 不能同时指定 CA 证书和跳过 TLS 校验")

    if user.get("exec"):
        raise ValueError("不执行 kubeconfig exec 插件；请改用静态 Token 或内嵌客户端证书")
    if user.get("auth-provider"):
        raise ValueError("不支持 kubeconfig auth-provider 动态认证；请改用静态 Token 或内嵌客户端证书")
    if user.get("tokenFile"):
        raise ValueError("不读取 kubeconfig 外部 tokenFile；请直接提供静态 Token")
    if user.get("client-certificate") or user.get("client-key"):
        raise ValueError("不读取 kubeconfig 外部证书文件；请提供内嵌 client-certificate-data/client-key-data")
    if user.get("username") or user.get("password"):
        raise ValueError("不支持 kubeconfig 用户名/密码认证")

    token = user.get("token")
    cert_data = user.get("client-certificate-data")
    key_data = user.get("client-key-data")
    if token and (cert_data or key_data):
        raise ValueError("kubeconfig user 只能选择一种静态认证方式")
    if token:
        if not isinstance(token, str) or len(token) > 65536 or "\r" in token or "\n" in token:
            raise ValueError("kubeconfig Token 无效")
        credential = {"token": token, "ca_pem": ca_pem, "insecure_skip_tls_verify": insecure}
        return server.rstrip("/"), "sa_token", credential
    if token not in (None, ""):
        raise ValueError("kubeconfig Token 无效")
    if cert_data or key_data:
        cert = _decode_data(cert_data, "client-certificate-data", required=True)
        key = _decode_data(key_data, "client-key-data", required=True)
        credential = {"client_cert_pem": cert, "client_key_pem": key,
                      "ca_pem": ca_pem, "insecure_skip_tls_verify": insecure}
        return server.rstrip("/"), "client_cert", credential
    raise ValueError("kubeconfig user 必须包含静态 token 或内嵌客户端证书/私钥")
