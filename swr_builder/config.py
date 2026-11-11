"""平台配置：读取 config.yaml，敏感信息可以用环境变量覆盖。"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml
from .cloud.hcs import HCSProfile

logger = logging.getLogger("swr_builder")


@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8080
    # 首次启动时创建的管理员 admin 的密码；为空则随机生成并写入日志和 data/admin-initial-password.txt
    admin_password: str = ""
    # 通过 HTTPS 访问时设为 true，登录 Cookie 只在 HTTPS 下发送
    secure_cookie: bool = False
    # 兼容 1.0 的配置：{用户名: 密码}，首次启动时导入为管理员账号
    users: Dict[str, str] = field(default_factory=dict)
    # 是否开放 /docs、/redoc、/openapi.json（需登录）；默认关闭
    api_docs: bool = False


@dataclass
class RegistryConfig:
    address: str = ""          # SWR 服务地址，如 swr.ndc302-region-1.huaweicloud.cnnc.cn
    organization: str = ""     # 默认组织，应用里可以单独覆盖
    username: str = ""         # docker login 用户名：区域项目名@AK
    password: str = ""         # docker login 密码：长期有效登录密钥（明文）
    ak: str = ""               # 可选：只有 AK/SK 时，平台按 SWR 规则自动计算登录密钥
    sk: str = ""
    project: str = ""          # 可选：AK/SK 模式下的区域项目名，如 ndc302-region-1_his_beta
    insecure: bool = False     # 仅平台 Registry API；Docker daemon TLS 另行配置
    ca_file: str = ""          # Registry HTTP API 的 CA；Docker daemon 单独配置
    timeout: float = 30.0

    @property
    def host(self) -> str:
        addr = self.address.strip()
        for prefix in ("https://", "http://"):
            if addr.lower().startswith(prefix):
                addr = addr[len(prefix):]
        return addr.rstrip("/")

    @property
    def base_url(self) -> str:
        scheme = "http" if self.address.strip().lower().startswith("http://") else "https"
        return f"{scheme}://{self.host}"

    def credentials(self) -> Tuple[str, str]:
        """返回 (用户名, 密码)。优先使用 password；否则用 AK/SK 计算长期登录密钥。"""
        if self.password:
            return self.username, self.password
        if self.ak and self.sk:
            user = self.username or (f"{self.project}@{self.ak}" if self.project else self.ak)
            # 与 SWR 文档的命令等价：printf "$AK" | openssl dgst -binary -sha256 -hmac "$SK" | od ...
            key = hmac.new(self.sk.encode(), self.ak.encode(), hashlib.sha256).hexdigest()
            return user, key
        return self.username, ""


@dataclass
class BuildConfig:
    docker_bin: str = "docker"
    data_dir: str = "./data"           # 数据目录（数据库、日志、构建上下文），相对路径以启动目录为准
    max_concurrent: int = 2            # 同时运行的构建数；同一个镜像仓库始终串行
    login_timeout: int = 120
    build_timeout: int = 1800
    push_timeout: int = 1800
    max_upload_mb: int = 2048
    keep_context: bool = False         # 构建结束后是否保留构建上下文（上传的文件）
    cleanup_local_images: bool = True  # 推送成功后删除本地的上一版本标签，避免构建机磁盘膨胀
    extra_build_args: List[str] = field(default_factory=list)  # 追加给 docker build 的参数


@dataclass
class SecurityConfig:
    session_hours: int = 12            # 登录有效期（小时）
    remember_days: int = 7             # 勾选“记住我”时的登录有效期（天）
    password_min_length: int = 8
    max_login_failures: int = 5        # 连续失败次数达到后锁定，0 表示不锁定
    lock_minutes: int = 15


@dataclass
class GeneralConfig:
    site_name: str = "SWR 镜像构建平台"
    deploy_enabled: bool = True


@dataclass
class DatabaseConfig:
    # PostgreSQL 14+，或 PG 兼容模式（DBCOMPATIBILITY 'PG'）的 GaussDB / openGauss。
    # 例如 postgresql://swr@127.0.0.1:5432/swr；口令建议用环境变量 BUILDER_DATABASE_PASSWORD 单独提供
    url: str = ""
    password: str = ""
    # psycopg（默认）：PostgreSQL，以及允许 MD5（password_encryption_type=0/1）认证的 GaussDB；
    # gaussdb：华为 GaussDB Python 驱动，需另装 GaussDB 客户端 libpq，可用 SHA256 认证
    driver: str = ""
    pool_size: int = 10
    connect_timeout: int = 10


@dataclass
class Settings:
    server: ServerConfig = field(default_factory=ServerConfig)
    registry: RegistryConfig = field(default_factory=RegistryConfig)
    build: BuildConfig = field(default_factory=BuildConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    general: GeneralConfig = field(default_factory=GeneralConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    hcs: HCSProfile = field(default_factory=HCSProfile)
    apps: List[Dict[str, Any]] = field(default_factory=list)  # 首次启动时导入的应用
    config_path: Optional[str] = None
    # 由环境变量设置的配置项（如 registry.password），页面上只读
    env_locked: Dict[str, str] = field(default_factory=dict)

    @property
    def data_path(self) -> Path:
        return Path(self.build.data_dir).expanduser().resolve()

    @property
    def database_url(self) -> str:
        """配置文件中的连接串；未配置时读取环境变量 BUILDER_DATABASE_URL（便于测试与容器部署）。"""
        url = self.database.url or os.environ.get("BUILDER_DATABASE_URL", "")
        if not url:
            raise RuntimeError("未配置数据库：请在 config.yaml 的 database.url 或环境变量 BUILDER_DATABASE_URL 中"
                               "填写 PostgreSQL / GaussDB 连接串")
        return url

    @property
    def database_password(self) -> str:
        return self.database.password or os.environ.get("BUILDER_DATABASE_PASSWORD", "")

    @property
    def database_driver(self) -> str:
        return self.database.driver or os.environ.get("BUILDER_DATABASE_DRIVER", "") or "psycopg"


ENV_OVERRIDES = {
    "HCS_AK": ("hcs", "ak"),
    "HCS_SK": ("hcs", "sk"),
    "HCS_SECURITY_TOKEN": ("hcs", "security_token"),
    "HCS_PROJECT_ID": ("hcs", "project_id"),
    "HCS_DOMAIN_ID": ("hcs", "domain_id"),
    "HCS_PASSWORD": ("hcs", "password"),
    "HCS_TOKEN": ("hcs", "token"),
    "HCS_IAM_ENDPOINT": ("hcs", "iam_endpoint"),
    "SWR_ADDRESS": ("registry", "address"),
    "SWR_ORGANIZATION": ("registry", "organization"),
    "SWR_USERNAME": ("registry", "username"),
    "SWR_PASSWORD": ("registry", "password"),
    "SWR_AK": ("registry", "ak"),
    "SWR_SK": ("registry", "sk"),
    "SWR_PROJECT": ("registry", "project"),
    "BUILDER_DATA_DIR": ("build", "data_dir"),
    "BUILDER_DOCKER_BIN": ("build", "docker_bin"),
    "BUILDER_PORT": ("server", "port"),
    "BUILDER_ADMIN_PASSWORD": ("server", "admin_password"),
    "BUILDER_DATABASE_URL": ("database", "url"),
    "BUILDER_DATABASE_PASSWORD": ("database", "password"),
    "BUILDER_DATABASE_DRIVER": ("database", "driver"),
}


def _coerce(current: Any, value: Any, name: str) -> Any:
    if isinstance(current, bool):
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)
    if isinstance(current, int):
        return int(value)
    if isinstance(current, float):
        return float(value)
    if isinstance(current, list):
        if isinstance(value, str):
            return [v for v in value.split() if v]
        return [str(v) for v in (value or [])]
    if isinstance(current, dict):
        if not isinstance(value, dict):
            raise ValueError(f"配置项 {name} 必须是映射")
        return {str(k): str(v) for k, v in value.items()}
    return "" if value is None else str(value)


def _fill(section: Any, data: Optional[Dict[str, Any]], prefix: str) -> None:
    if not data:
        return
    if not isinstance(data, dict):
        raise ValueError(f"配置段 {prefix} 格式错误")
    known = {f.name for f in fields(section)}
    for key, value in data.items():
        if key not in known:
            logger.warning("忽略未知配置项 %s.%s", prefix, key)
            continue
        if value is None:
            continue
        setattr(section, key, _coerce(getattr(section, key), value, f"{prefix}.{key}"))


def load_settings(path: Optional[str] = None) -> Settings:
    path = path or os.environ.get("BUILDER_CONFIG") or "config.yaml"
    settings = Settings()
    cfg_file = Path(path)
    data: Dict[str, Any] = {}
    if cfg_file.exists():
        data = yaml.safe_load(cfg_file.read_text(encoding="utf-8")) or {}
        settings.config_path = str(cfg_file.resolve())
    else:
        logger.warning("未找到配置文件 %s，使用默认配置 + 环境变量", path)
    _fill(settings.server, data.get("server"), "server")
    _fill(settings.registry, data.get("registry"), "registry")
    _fill(settings.build, data.get("build"), "build")
    _fill(settings.security, data.get("security"), "security")
    _fill(settings.general, data.get("general"), "general")
    _fill(settings.database, data.get("database"), "database")
    _fill(settings.hcs, data.get("hcs"), "hcs")
    settings.apps = list(data.get("apps") or [])
    for env, (section, key) in ENV_OVERRIDES.items():
        if os.environ.get(env):
            obj = getattr(settings, section)
            setattr(obj, key, _coerce(getattr(obj, key), os.environ[env], env))
            settings.env_locked[f"{section}.{key}"] = env
    return settings
