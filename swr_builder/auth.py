"""账号、登录会话、API 令牌与角色权限。"""
from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .config import Settings
from .security import (API_TOKEN_PREFIX, DUMMY_HASH, check_password_policy, generate_password, hash_password,
                       new_api_token, new_session_token, token_hash, verify_password)
from .store import Store, utcnow

logger = logging.getLogger("swr_builder")

SESSION_COOKIE = "swr_session"
USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@-]{1,31}$")

ROLES: Dict[str, str] = {"admin": "管理员", "developer": "开发者", "viewer": "只读用户"}

# 权限点：新增功能时在这里登记，再分配给角色
PERMISSION_LABELS: Dict[str, str] = {
    "app.view": "查看应用和版本",
    "app.manage": "新建/修改/删除应用",
    "build.run": "上传文件并构建镜像",
    "job.view": "查看构建记录和日志",
    "job.cancel.own": "取消自己提交的构建",
    "job.cancel": "取消任何人的构建",
    "deploy.view": "查看部署目标和部署记录",
    "deploy.run": "执行部署",
    "deploy.manage": "管理部署目标",
    "settings.manage": "修改系统设置",
    "user.manage": "管理账号",
    "audit.view": "查看操作审计",
    "project.view": "查看项目",
    "project.manage": "管理项目",
    "project.member.manage": "管理项目成员",
    "cluster.manage": "管理集群",
    "namespace.view": "查看命名空间",
    "namespace.member.manage": "管理命名空间成员",
    "service.config": "配置服务",
    "service.deploy": "发布服务",
    "service.operate": "运维服务",
    "service.rollback": "回滚服务",
    "service.delete": "删除服务",
    "secret.manage": "管理服务密钥",
    "deploy.approve": "审批发布",
    "pod.log.view": "查看 Pod 日志",
}
ROLE_PERMISSIONS: Dict[str, set] = {
    "admin": {"*"},
    "developer": {"app.view", "build.run", "job.view", "job.cancel.own", "deploy.view", "deploy.run"},
    "viewer": {"app.view", "job.view", "deploy.view"},
}


def has_perm(role: str, perm: str) -> bool:
    perms = ROLE_PERMISSIONS.get(role, set())
    return "*" in perms or perm in perms


def permissions_of(role: str) -> List[str]:
    return [p for p in PERMISSION_LABELS if has_perm(role, p)]


def _parse(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _later(**delta: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(**delta)).isoformat(timespec="seconds")


def public_user(user: Dict[str, Any]) -> Dict[str, Any]:
    """返回给前端的账号信息（不含密码哈希）。"""
    return {
        "id": user["id"], "username": user["username"], "display_name": user.get("display_name") or user["username"],
        "email": user.get("email") or "", "role": user["role"], "role_label": ROLES.get(user["role"], user["role"]),
        "enabled": bool(user["enabled"]), "must_change_password": bool(user["must_change_password"]),
        "created_at": user.get("created_at"), "last_login_at": user.get("last_login_at"),
        "password_changed_at": user.get("password_changed_at"),
    }


class AuthError(Exception):
    def __init__(self, message: str, status: int = 401):
        super().__init__(message)
        self.status = status


class AuthService:
    def __init__(self, settings: Settings, store: Store):
        self.settings = settings
        self.store = store
        self._failures: Dict[str, List[float]] = {}
        self._locked_until: Dict[str, float] = {}

    @property
    def initial_password_file(self) -> Path:
        return self.settings.data_path / "admin-initial-password.txt"

    # ------------------------------------------------------------------ 初始化
    def bootstrap(self) -> Optional[str]:
        """首次启动创建管理员。返回随机生成的初始密码（没有生成则返回 None）。"""
        if self.store.count_users():
            return None
        legacy = self.settings.server.users
        if legacy:  # 兼容 1.0 的 server.users 配置
            for name, password in legacy.items():
                if USERNAME_RE.match(name):
                    self.store.create_user(username=name, display_name=name, password_hash=hash_password(password),
                                           role="admin", enabled=1, must_change_password=1)
                    logger.info("已从配置文件 server.users 导入管理员账号 %s", name)
            if self.store.count_users():
                return None
        password = self.settings.server.admin_password
        generated = not password
        if generated:
            password = generate_password(max(12, self.settings.security.password_min_length))
        self.store.create_user(username="admin", display_name="管理员", password_hash=hash_password(password),
                               role="admin", enabled=1, must_change_password=1 if generated else 0)
        if generated:
            path = self.initial_password_file
            path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(f"用户名：admin\n初始密码：{password}\n首次登录后需要修改密码，修改后本文件会自动删除。\n")
            logger.warning("已创建初始管理员 admin，请从 %s 读取初始密码，首次登录后需修改", path)
            return password
        logger.info("已创建管理员 admin（密码来自 server.admin_password / BUILDER_ADMIN_PASSWORD）")
        return None

    # ------------------------------------------------------------------ 登录
    def _lock_key(self, username: str, ip: str) -> str:
        return f"{username.strip().lower()}|{ip}"

    def _check_locked(self, key: str) -> None:
        until = self._locked_until.get(key, 0)
        if until > time.time():
            minutes = max(1, int((until - time.time() + 59) // 60))
            raise AuthError(f"登录失败次数过多，请 {minutes} 分钟后再试", 429)

    def _record_failure(self, key: str) -> None:
        sec = self.settings.security
        if sec.max_login_failures <= 0:
            return
        now = time.time()
        window = sec.lock_minutes * 60
        attempts = [t for t in self._failures.get(key, []) if now - t < window] + [now]
        self._failures[key] = attempts
        if len(attempts) >= sec.max_login_failures:
            self._locked_until[key] = now + window
            self._failures.pop(key, None)

    def authenticate(self, username: str, password: str, ip: str) -> Dict[str, Any]:
        key = self._lock_key(username, ip)
        self._check_locked(key)
        user = self.store.get_user_by_name(username.strip()) if username.strip() else None
        valid = verify_password(password, user["password_hash"] if user else DUMMY_HASH) and user is not None
        if not valid:
            self._record_failure(key)
            self._check_locked(key)
            raise AuthError("用户名或密码错误")
        if not user["enabled"]:  # type: ignore[index]
            raise AuthError("账号已停用，请联系管理员", 403)
        self._failures.pop(key, None)
        self.store.update_user(user["id"], last_login_at=utcnow())  # type: ignore[index]
        return self.store.get_user(user["id"])  # type: ignore[index,return-value]

    def unlock(self, username: str) -> None:
        prefix = username.strip().lower() + "|"
        for key in [k for k in self._locked_until if k.startswith(prefix)]:
            self._locked_until.pop(key, None)

    def create_session(self, user_id: int, remember: bool, ip: str, user_agent: str) -> Tuple[str, int]:
        sec = self.settings.security
        seconds = sec.remember_days * 86400 if remember else sec.session_hours * 3600
        token = new_session_token()
        self.store.create_session(token_hash(token), user_id, _later(seconds=seconds), ip, user_agent)
        if int(time.time()) % 20 == 0:
            self.store.purge_expired_sessions()
        return token, seconds

    def resolve_session(self, token: str) -> Optional[Tuple[Dict[str, Any], str]]:
        hashed = token_hash(token)
        session = self.store.get_session(hashed)
        if not session:
            return None
        expires = _parse(session["expires_at"])
        if not expires or expires <= datetime.now(timezone.utc):
            self.store.delete_session(hashed)
            return None
        user = self.store.get_user(session["user_id"])
        if not user or not user["enabled"]:
            return None
        last = _parse(session.get("last_seen_at"))
        if not last or (datetime.now(timezone.utc) - last).total_seconds() > 300:
            self.store.touch_session(hashed)
        return user, hashed

    def resolve_api_token(self, token: str) -> Optional[Dict[str, Any]]:
        if not token.startswith(API_TOKEN_PREFIX):
            return None
        record = self.store.find_api_token(token_hash(token))
        if not record:
            return None
        expires = _parse(record.get("expires_at"))
        if expires and expires <= datetime.now(timezone.utc):
            return None
        user = self.store.get_user(record["user_id"])
        if not user or not user["enabled"]:
            return None
        last = _parse(record.get("last_used_at"))
        if not last or (datetime.now(timezone.utc) - last).total_seconds() > 60:
            self.store.touch_api_token(record["id"])
        return user

    def logout(self, session_hash: Optional[str]) -> None:
        if session_hash:
            self.store.delete_session(session_hash)

    # ------------------------------------------------------------------ 账号维护
    def validate_password(self, password: str) -> None:
        check_password_policy(password, self.settings.security.password_min_length)

    def change_password(self, user: Dict[str, Any], old: str, new: str, keep_session: Optional[str]) -> None:
        if not verify_password(old, user["password_hash"]):
            raise AuthError("当前密码不正确", 400)
        if old == new:
            raise AuthError("新密码不能与当前密码相同", 400)
        try:
            self.validate_password(new)
        except ValueError as exc:
            raise AuthError(str(exc), 400)
        self.store.update_user(user["id"], password_hash=hash_password(new), must_change_password=0,
                               password_changed_at=utcnow())
        self.store.delete_user_api_tokens(user["id"])
        self.store.delete_user_sessions(user["id"], keep=keep_session)  # 其他设备需要重新登录
        if user["username"] == "admin" and self.initial_password_file.exists():
            self.initial_password_file.unlink()

    def reset_password(self, user_id: int, password: Optional[str]) -> str:
        if password:
            self.validate_password(password)
        else:
            password = generate_password(max(12, self.settings.security.password_min_length))
        self.store.update_user(user_id, password_hash=hash_password(password), must_change_password=1,
                               password_changed_at=utcnow())
        self.store.delete_user_sessions(user_id)
        self.store.delete_user_api_tokens(user_id)
        return password

    def create_api_token(self, user_id: int, name: str, expires_days: int) -> Tuple[str, int]:
        token = new_api_token()
        expires = _later(days=expires_days) if expires_days > 0 else None
        token_id = self.store.create_api_token(user_id, name, token_hash(token), token[:9], expires)
        return token, token_id
