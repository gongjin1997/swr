"""验证交付镜像的架构、全部常用扩展依赖和本机时区数据。"""
import importlib
import json
import platform
import subprocess
from zoneinfo import ZoneInfo

MODULES = (
    "fastapi", "uvicorn", "multipart", "httpx", "yaml", "cryptography", "requests",
    "ruamel.yaml", "jsonschema", "jinja2", "dotenv", "pydantic_settings", "tomli",
    "tomli_w", "orjson", "jwt", "tenacity", "paramiko", "kubernetes", "tzdata",
    "dateutil", "croniter", "email_validator", "sqlalchemy", "alembic", "websockets",
    "rich", "huaweicloudsdkcore", "huaweicloudsdkiam.v3", "huaweicloudsdkswr.v2",
    "huaweicloudsdkcce.v3", "huaweicloudsdkecs.v2",
)

if __name__ == "__main__":
    for module in MODULES:
        importlib.import_module(module)
    import yaml
    assert yaml.safe_load("service:\n  enabled: true\n")["service"]["enabled"] is True
    assert ZoneInfo("Asia/Shanghai").key == "Asia/Shanghai"
    assert platform.machine() in ("aarch64", "arm64"), platform.machine()
    versions = {}
    for name, command in {
        "docker": ["docker", "--version"], "buildx": ["docker", "buildx", "version"],
        "compose": ["docker", "compose", "version"], "node": ["node", "--version"],
        "npm": ["npm", "--version"],
    }.items():
        versions[name] = subprocess.check_output(command, text=True).strip()
    print(json.dumps({"architecture": platform.machine(), "imports": len(MODULES),
                      "python": platform.python_version(), "tools": versions}, ensure_ascii=False))
