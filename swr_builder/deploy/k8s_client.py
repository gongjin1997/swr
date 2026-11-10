"""纳管集群的 Kubernetes API 客户端；支持静态 Bearer Token 与 TLS 客户端证书。"""
from __future__ import annotations

import ssl
import tempfile
from typing import Any, Dict, Optional
from urllib.parse import quote, urlsplit
from pathlib import Path

import httpx

from ..errors import DomainError, Forbidden, Unavailable


def validate_api_server(server: str) -> None:
    """API Server 必须是不带凭据、查询串和片段的 HTTPS 地址。"""
    parsed = urlsplit(server)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment):
        raise DomainError("API Server 必须是无凭据的 HTTPS 地址")


def deployment_path(namespace: str, name: str) -> str:
    return "/apis/apps/v1/namespaces/" + quote(namespace, safe="") + "/deployments/" + quote(name, safe="")


def build_ssl_context(credential: Dict[str, Any]) -> ssl.SSLContext:
    if credential.get("insecure_skip_tls_verify"):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    elif credential.get("ca_pem"):
        context = ssl.create_default_context(cadata=credential["ca_pem"])
    else:
        context = ssl.create_default_context()

    cert, key = credential.get("client_cert_pem"), credential.get("client_key_pem")
    if bool(cert) != bool(key):
        raise ValueError("客户端证书和私钥必须同时提供")
    if cert:
        # OpenSSL loads the key material before the private temporary files are removed.
        with tempfile.TemporaryDirectory(prefix="swr-kube-cert-") as directory:
            cert_path, key_path = Path(directory) / "client.crt", Path(directory) / "client.key"
            cert_path.write_text(cert, encoding="ascii")
            key_path.write_text(key, encoding="ascii")
            cert_path.chmod(0o600)
            key_path.chmod(0o600)
            context.load_cert_chain(str(cert_path), str(key_path))
    return context


class KubeClient:
    """用法：`async with KubeClient(server, credential, transport) as kube: ...`。"""

    def __init__(self, server: str, credential: Dict[str, Any], transport: Any = None, timeout: float = 10):
        self.base = server.rstrip("/")
        self._token = credential.get("token", "")
        self._verify = build_ssl_context(credential)
        self._transport = transport
        self._timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "KubeClient":
        self._client = httpx.AsyncClient(verify=self._verify, timeout=self._timeout, transport=self._transport,
                                         follow_redirects=False)
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    @property
    def auth_headers(self) -> Dict[str, str]:
        return {"Authorization": "Bearer " + self._token} if self._token else {}

    async def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        assert self._client is not None, "KubeClient 需要在 async with 中使用"
        headers = {**self.auth_headers, **kwargs.pop("headers", {})}
        return await self._client.request(method, self.base + path, headers=headers, **kwargs)

    async def version(self) -> str:
        """GET /version，返回 gitVersion。连接或响应异常抛 Unavailable。"""
        try:
            response = await self.request("GET", "/version")
        except httpx.HTTPError:
            raise Unavailable("连接集群失败，请检查网络、TLS 和凭据") from None
        if response.status_code != 200:
            raise Unavailable(f"Kubernetes API 返回 HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError:
            raise Unavailable("Kubernetes API 版本响应格式错误") from None
        version = payload.get("gitVersion") if isinstance(payload, dict) else None
        if not isinstance(version, str):
            raise Unavailable("Kubernetes API 版本响应格式错误")
        return version

    async def namespace_status(self, name: str) -> int:
        try:
            response = await self.request("GET", "/api/v1/namespaces/" + name)
        except httpx.HTTPError:
            raise Unavailable("读取目标 namespace 失败，请检查集群连接") from None
        return response.status_code

    async def list_resources(self, path: str):
        """Read paginated Kubernetes lists without returning a silently truncated list."""
        items, continuation = [], ""
        for _ in range(100):
            try:
                response = await self.request("GET", path, params={"limit": 500, "continue": continuation})
            except httpx.HTTPError:
                raise Unavailable("读取集群资源失败，请检查网络、TLS 与凭据") from None
            if response.status_code == 403:
                raise Forbidden("集群凭据缺少 list 权限：" + path)
            if response.status_code != 200:
                raise Unavailable(f"读取集群资源失败，HTTP {response.status_code}：{path}")
            try:
                payload = response.json()
                batch = payload["items"]
                if not isinstance(batch, list) or any(not isinstance(x, dict) for x in batch):
                    raise ValueError()
                continuation = payload.get("metadata", {}).get("continue", "")
                if not isinstance(continuation, str):
                    raise ValueError()
            except (ValueError, KeyError, TypeError, AttributeError):
                raise Unavailable("集群资源列表响应格式无效") from None
            items.extend(batch)
            if not continuation:
                return items
        raise Unavailable("集群资源过多，未能完整读取，请缩小查询范围")

    async def access_review(self, namespace: str, group: str, resource: str, verb: str) -> Dict[str, Any]:
        """SelfSubjectAccessReview：当前凭据能否在 namespace 中对 resource 执行 verb。返回 status 对象。"""
        body = {"apiVersion": "authorization.k8s.io/v1", "kind": "SelfSubjectAccessReview",
                "spec": {"resourceAttributes": {"namespace": namespace, "verb": verb, "group": group,
                                                "resource": resource}}}
        try:
            response = await self.request("POST", "/apis/authorization.k8s.io/v1/selfsubjectaccessreviews",
                                          headers={"Content-Type": "application/json"}, json=body)
        except httpx.HTTPError:
            raise Unavailable("集群权限自检连接失败") from None
        if response.status_code not in (200, 201):
            raise Unavailable(f"SSAR 请求失败，HTTP {response.status_code}")
        try:
            return response.json().get("status", {})
        except (ValueError, TypeError, AttributeError):
            raise Unavailable("SSAR 响应格式无效") from None
