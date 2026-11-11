"""证书与密钥文件库。

附件中的证书 Secret 有 12 个文件：各中间件 CA、客户端证书与私钥、JKS / PKCS#12 信任库、根密钥文件。
这些文件统一上传到这里，每次上传是一个新版本；配置集的敏感文件、敏感环境变量按版本引用它们。
- X.509（PEM / DER）解析主题、签发者、SAN、序列号、有效期与 SHA-256 指纹；PKCS#12 提供口令时解析，口令不保存；
  JKS 没有可用的解析库，按文件保存，有效期可以手工填写；
- 文件内容加密保存；下载需要全局 secret.view 权限，调用方记审计。
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from ..errors import NotFound
from ..security import SecretBox
from ..store import Store
from ..validation import CONFIG_KEY_RE, reject_unknown, text_field

try:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.serialization import pkcs12
except ImportError:  # pragma: no cover - 正式环境依赖 cryptography>=42；精简的开发环境可能没有 x509 模块
    x509 = None

KINDS = ("x509", "pkcs12", "jks", "key", "file")
KIND_LABELS = {"x509": "X.509 证书", "pkcs12": "PKCS#12 证书包", "jks": "Java 密钥库", "key": "私钥", "file": "其他文件"}
MAX_FILE_BYTES = 512 * 1024
NAME_RE = re.compile(r"[^\s/\\][^/\\]{0,127}")
EXPIRING_DAYS, WARNING_DAYS = 30, 90
_JKS_MAGIC = (b"\xfe\xed\xfe\xed", b"\xce\xce\xce\xce")


def _utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _certificate_metadata(cert: Any, chain: int = 1) -> Dict[str, Any]:
    names: List[str] = []
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        names = [str(v) for v in san.get_values_for_type(x509.DNSName)]
        names += [str(v) for v in san.get_values_for_type(x509.IPAddress)]
    except x509.ExtensionNotFound:
        pass
    key = cert.public_key()
    return {
        "subject": cert.subject.rfc4514_string(), "issuer": cert.issuer.rfc4514_string(),
        "serial": format(cert.serial_number, "x"), "not_before": _utc(cert.not_valid_before_utc),
        "not_after": _utc(cert.not_valid_after_utc), "san": names[:50],
        "fingerprint_sha256": cert.fingerprint(hashes.SHA256()).hex(":").upper(),
        "key_type": type(key).__name__.lstrip("_").replace("PublicKey", ""),
        "key_size": getattr(key, "key_size", None), "chain_length": chain,
        "self_signed": cert.subject == cert.issuer,
    }


def inspect_file(filename: str, content: bytes, password: Optional[str] = None) -> Tuple[str, Dict[str, Any]]:
    """识别文件类型并提取元数据。返回 (kind, metadata)；metadata.not_after 为证书到期时间。"""
    if content[:4] in _JKS_MAGIC:
        return "jks", {"format": "JKS" if content[:4] == _JKS_MAGIC[0] else "JCEKS"}
    has_cert = b"-----BEGIN CERTIFICATE-----" in content
    has_key = b"PRIVATE KEY-----" in content
    if (has_cert or has_key) and x509 is None:
        raise RuntimeError("服务器缺少 cryptography，无法解析证书")
    if has_cert:
        certs = x509.load_pem_x509_certificates(content)
        metadata = _certificate_metadata(certs[0], len(certs))
        metadata["contains_private_key"] = has_key
        return "x509", metadata
    if has_key:
        try:
            key = serialization.load_pem_private_key(content, password=None)
            return "key", {"key_type": type(key).__name__.lstrip("_").replace("PrivateKey", ""),
                           "key_size": getattr(key, "key_size", None), "encrypted": False}
        except TypeError:
            return "key", {"encrypted": True}
        except ValueError as exc:
            raise ValueError(f"私钥无法解析：{exc}") from None
    if x509 is None:
        return "file", {}
    try:
        return "x509", _certificate_metadata(x509.load_der_x509_certificate(content))
    except ValueError:
        pass
    lowered = filename.lower()
    if lowered.endswith((".p12", ".pfx")) or password:
        try:
            _, cert, extra = pkcs12.load_key_and_certificates(content, password.encode() if password else None)
        except ValueError:
            return "pkcs12", {"encrypted": True, "parsed": False}
        metadata = _certificate_metadata(cert, 1 + len(extra or [])) if cert else {}
        metadata.update(encrypted=bool(password), parsed=True)
        return "pkcs12", metadata
    return "file", {}


def expiry(not_after: Optional[str]) -> Dict[str, Any]:
    if not not_after:
        return {"status": "unknown", "days_left": None}
    try:
        moment = datetime.fromisoformat(not_after.replace("Z", "+00:00"))
    except ValueError:
        return {"status": "unknown", "days_left": None}
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    days = (moment - datetime.now(timezone.utc)).days
    status = "expired" if days < 0 else "expiring" if days <= EXPIRING_DAYS else "warning" if days <= WARNING_DAYS else "ok"
    return {"status": status, "days_left": days}


def _parse_manual_expiry(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ValueError("not_after 必须是日期字符串，例如 2027-01-31")
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("not_after 必须是日期，例如 2027-01-31") from None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return _utc(moment)


def _version_payload(box: SecretBox, data: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    filename = data.get("filename")
    if not isinstance(filename, str) or not CONFIG_KEY_RE.fullmatch(filename):
        raise ValueError("文件名只能包含字母、数字、-、_、.（会作为 Secret 中的文件名）")
    encoded = data.get("content_base64")
    if not isinstance(encoded, str) or not encoded:
        raise ValueError("缺少文件内容 content_base64")
    try:
        content = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("content_base64 不是有效的 Base64") from None
    if not content or len(content) > MAX_FILE_BYTES:
        raise ValueError(f"文件大小必须在 1 字节到 {MAX_FILE_BYTES // 1024} KiB 之间")
    password = data.get("password")
    if password is not None and not isinstance(password, str):
        raise ValueError("password 必须是字符串")
    kind, metadata = inspect_file(filename, content, password or None)
    not_after = metadata.get("not_after")
    manual = _parse_manual_expiry(data.get("not_after"))
    if manual and not not_after:
        not_after = manual
        metadata["not_after"] = manual
        metadata["not_after_source"] = "manual"
    return kind, {"filename": filename, "ciphertext": box.encrypt(base64.b64encode(content).decode("ascii")),
                  "size": len(content), "sha256": hashlib.sha256(content).hexdigest(), "metadata": metadata,
                  "not_after": not_after, "note": text_field(data, "note", 300)}


def public_certificate(item: Dict[str, Any]) -> Dict[str, Any]:
    result = {k: item.get(k) for k in ("id", "name", "kind", "description", "version", "filename", "size", "sha256",
                                       "metadata", "not_after", "used_by", "created_at", "updated_at")}
    result["kind_label"] = KIND_LABELS.get(item["kind"], item["kind"])
    result.update(expiry(item.get("not_after")))
    return result


def list_certificates(store: Store) -> List[Dict[str, Any]]:
    return [public_certificate(item) for item in store.list_certificates()]


def certificate_or_404(store: Store, certificate_id: int) -> Dict[str, Any]:
    item = store.get_certificate(certificate_id)
    if not item:
        raise NotFound("证书不存在")
    return item


def certificate_detail(store: Store, certificate_id: int) -> Dict[str, Any]:
    item = public_certificate(certificate_or_404(store, certificate_id))
    item["versions"] = [dict({k: v.get(k) for k in ("number", "filename", "size", "sha256", "metadata", "not_after",
                                                     "note", "created_at", "created_by_username")},
                             **expiry(v.get("not_after"))) for v in store.list_certificate_versions(certificate_id)]
    item["usage"] = store.certificate_usage(certificate_id)
    return item


def create_certificate(store: Store, box: SecretBox, actor_id: int, data: Any) -> Dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("请求体必须是对象")
    reject_unknown(data, ("name", "description", "filename", "content_base64", "password", "not_after", "note"),
                   "包含不支持的字段")
    name = text_field(data, "name", 128) or str(data.get("filename") or "")
    if not NAME_RE.fullmatch(name):
        raise ValueError("证书名称不能为空，不能包含 / 或 \\，最长 128 字符")
    kind, version = _version_payload(box, data)
    certificate_id = store.create_certificate(name, kind, text_field(data, "description", 300), version, actor_id)
    return certificate_detail(store, certificate_id)


def rotate_certificate(store: Store, box: SecretBox, actor_id: int, certificate_id: int, data: Any) -> Dict[str, Any]:
    certificate_or_404(store, certificate_id)
    if not isinstance(data, dict):
        raise ValueError("请求体必须是对象")
    reject_unknown(data, ("base_version", "filename", "content_base64", "password", "not_after", "note"),
                   "包含不支持的字段")
    base_version = data.get("base_version")
    if not isinstance(base_version, int) or isinstance(base_version, bool):
        raise ValueError("base_version 必须是整数")
    kind, version = _version_payload(box, {k: v for k, v in data.items() if k != "base_version"})
    store.add_certificate_version(certificate_id, base_version, kind, version, actor_id)
    return certificate_detail(store, certificate_id)


def certificate_content(store: Store, box: SecretBox, certificate_id: int, number: int) -> Dict[str, Any]:
    certificate_or_404(store, certificate_id)
    version = store.get_certificate_version(certificate_id, number=number)
    if not version:
        raise NotFound("证书版本不存在")
    return {"filename": version["filename"], "number": version["number"],
            "content_base64": box.decrypt(version["ciphertext"])}


def certificate_options(store: Store) -> List[Dict[str, Any]]:
    """配置页选择证书用：不含元数据细节。"""
    return [{"id": c["id"], "name": c["name"], "kind": c["kind"], "kind_label": KIND_LABELS.get(c["kind"], c["kind"]),
             "version": c["version"], "filename": c["filename"], **expiry(c.get("not_after"))}
            for c in store.list_certificates()]
