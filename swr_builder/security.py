"""密码哈希、访问令牌、敏感配置加密（Fernet 认证加密）。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
from pathlib import Path
from cryptography.fernet import Fernet, InvalidToken

PBKDF2_ITERATIONS = 200_000
API_TOKEN_PREFIX = "swrb_"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_password(password: str, iterations: int = PBKDF2_ITERATIONS) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt, digest = (stored or "").split("$")
        if algo != "pbkdf2_sha256":
            return False
        calc = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), _unb64(salt), int(iterations))
        return hmac.compare_digest(calc, _unb64(digest))
    except (ValueError, TypeError):
        return False


# 用于“用户不存在”时也执行一次哈希计算，避免通过响应时间判断用户名是否存在
DUMMY_HASH = hash_password(secrets.token_urlsafe(12))


def check_password_policy(password: str, min_length: int) -> None:
    if len(password) < min_length:
        raise ValueError(f"密码长度至少 {min_length} 位")
    if len(password) > 128:
        raise ValueError("密码不能超过 128 位")
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        raise ValueError("密码需要同时包含字母和数字")


def generate_password(length: int = 12) -> str:
    """生成满足密码策略的随机密码（去掉易混淆字符）。"""
    letters = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ"
    digits = "23456789"
    chars = [secrets.choice(letters), secrets.choice(digits)]
    chars += [secrets.choice(letters + digits) for _ in range(max(length, 8) - 2)]
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def new_api_token() -> str:
    return API_TOKEN_PREFIX + secrets.token_urlsafe(30)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class SecretBox:
    """敏感配置使用 cryptography.Fernet 认证加密。

    主密钥在 data/secret.key（0600）或 BUILDER_SECRET_KEY。
    enc:v1 仅为早期开发数据兼容读取；新保存值均为 enc:v2。
    """

    PREFIX = "enc:v2:"

    def __init__(self, key: bytes):
        self._fernet = Fernet(base64.urlsafe_b64encode(hmac.new(key,b"swr-builder/fernet",hashlib.sha256).digest()))
        self._enc_key = hmac.new(key, b"swr-builder/enc", hashlib.sha256).digest()
        self._mac_key = hmac.new(key, b"swr-builder/mac", hashlib.sha256).digest()

    @classmethod
    def load(cls, key_file: Path, env_key: str = "") -> "SecretBox":
        if env_key:
            return cls(hashlib.sha256(env_key.encode("utf-8")).digest())
        key_file = Path(key_file)
        if key_file.exists():
            key = _unb64(key_file.read_text(encoding="utf-8").strip())
            if len(key) != 32: raise ValueError("secret.key 格式错误，不能覆盖已有主密钥，请恢复正确备份")
            return cls(key)
        key = secrets.token_bytes(32)
        key_file.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(key_file), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(_b64(key))
        return cls(key)

    def _stream(self, nonce: bytes, length: int) -> bytes:
        out = bytearray()
        counter = 0
        while len(out) < length:
            out += hmac.new(self._enc_key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest()
            counter += 1
        return bytes(out[:length])

    def encrypt(self, text: str) -> str:
        return self.PREFIX + self._fernet.encrypt(text.encode("utf-8")).decode("ascii") if text else ""

    def decrypt(self, value: str) -> str:
        if not value:
            return ""
        if value.startswith(self.PREFIX):
            try: return self._fernet.decrypt(value[len(self.PREFIX):].encode("ascii")).decode("utf-8")
            except (InvalidToken,ValueError,UnicodeError):
                raise ValueError("无法解密已保存的密钥：主密钥可能变化或数据被篡改") from None
        if not value.startswith("enc:v1:"):
            return value  # 兼容手工写入的明文
        raw = _unb64(value[len("enc:v1:"):])
        nonce, cipher, tag = raw[:16], raw[16:-32], raw[-32:]
        expected = hmac.new(self._mac_key, nonce + cipher, hashlib.sha256).digest()
        if len(raw) < 48 or not hmac.compare_digest(tag, expected):
            raise ValueError("无法解密已保存的密钥：data/secret.key 或 BUILDER_SECRET_KEY 可能被更换")
        return bytes(a ^ b for a, b in zip(cipher, self._stream(nonce, len(cipher)))).decode("utf-8")
