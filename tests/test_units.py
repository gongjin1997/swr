import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from swr_builder.config import RegistryConfig, Settings  # noqa: E402
from swr_builder.builder import BuildError, BuildManager  # noqa: E402
from swr_builder.dockerfile import import_dockerfile  # noqa: E402
from swr_builder.versioning import increment_tag, latest_by_version, next_tag  # noqa: E402


def test_increment_tag():
    # 逢十进一（默认）：除第一位外每一位只用 0–9
    assert increment_tag("1.0.8") == "1.0.9"
    assert increment_tag("1.0.9") == "1.1.0"
    assert increment_tag("1.9.9") == "2.0.0"
    assert increment_tag("9.9.9") == "10.0.0"
    assert increment_tag("1.9") == "2.0"
    assert increment_tag("1.0.9-arm64") == "1.1.0-arm64"
    assert increment_tag("v1.0.9") == "v1.1.0"
    assert increment_tag("1.0.9-rc1") == "1.1.0-rc1"
    assert increment_tag("v12") == "v13"
    assert increment_tag("8-huawei-r30-arm64") == "9-huawei-r30-arm64"
    assert increment_tag("build20.9") == "build20.10"  # build20 不是版本号的一位
    assert increment_tag("1.0.15") == "1.1.0"  # 旧规则产生的两位数在下一次进位
    assert increment_tag("1.0.08") == "1.0.09" and increment_tag("1.0.09") == "1.1.00"  # 补零的位保持位数
    # 末位递增（应用可选）
    assert increment_tag("1.0.9", carry=False) == "1.0.10"
    assert increment_tag("1.0.9-arm64", carry=False) == "1.0.10-arm64"


def test_app_version_increment_setting():
    from swr_builder.apps import AppValidationError, normalize_app, version_carry
    base = {"name": "demo", "repository": "demo"}
    assert normalize_app(base)["version_increment"] == "carry" and version_carry(normalize_app(base))
    assert not version_carry(normalize_app({**base, "version_increment": "last"}))
    assert version_carry({"name": "old-app"})  # 早期保存、没有该字段的应用按逢十进一
    with pytest.raises(AppValidationError):
        normalize_app({**base, "version_increment": "fast"})


def test_latest_and_next():
    tags = ["1.0.2", "1.0.10", "1.0.9", "latest", "dev"]
    assert latest_by_version(tags) == "1.0.10"
    assert next_tag(tags, "1.0.10", "1.0.0") == "1.1.0"
    assert next_tag(tags, "1.0.10", "1.0.0", carry=False) == "1.0.11"
    assert next_tag(["1.0.8", "1.1.0"], "1.0.8") == "1.0.9"
    assert next_tag(["1.0.9", "1.1.0"], "1.0.9") == "1.1.1"  # 与已有标签冲突时继续递增
    assert next_tag([], None, "1.0.0") == "1.0.0"


def test_aksk_login_key():
    cfg = RegistryConfig(address="swr.example.com", ak="AK123", sk="SK456", project="cn-north-4")
    user, key = cfg.credentials()
    assert user == "cn-north-4@AK123"
    assert len(key) == 64


def test_docker_login_tls_failure_explains_daemon_setting():
    settings = Settings()
    settings.registry.address = "swr.example.com"
    settings.registry.username = "registry-user"
    settings.registry.password = "registry-password"
    settings.registry.insecure = True
    manager = object.__new__(BuildManager)
    manager.settings = settings

    async def fail_login(*_args, **_kwargs):
        return 1, "tls: failed to verify certificate: x509: certificate is not valid for any names"

    manager._run_cmd = fail_login

    class Log:
        def warn(self, _message):
            raise AssertionError("configured credentials must invoke docker login")

    with pytest.raises(BuildError, match="Docker daemon.*insecure-registries"):
        asyncio.run(manager._docker_login(1, Log()))


def test_import_dockerfile_crlf():
    text = ("FROM swr.example.com/metaerp_sdk/java:8-huawei-r30-arm64\r\nWORKDIR /app\r\n"
            "COPY --chown=nonroot:nonroot  a-1.0.jar app.jar\r\nCMD [\"-jar\",\"/app/app.jar\"]\r\n")
    result = import_dockerfile(text)
    assert result["base_image"].endswith("java:8-huawei-r30-arm64")
    assert result["target_dir"] == "/app"
    assert result["target_name"] == "app.jar"
    assert result["chown"] == "nonroot:nonroot"


def test_import_dockerfile_continuation():
    text = "FROM a.example.com/x/y:1 \\\n  AS base\nWORKDIR /app\nCOPY --chown=x:y \\\n  # note\n  a.jar b.jar\nCMD x\n"
    result = import_dockerfile(text)
    assert result["base_image"] == "a.example.com/x/y:1"
    assert (result["target_name"], result["chown"]) == ("b.jar", "x:y")
    assert result["full_template"] == "FROM {{BASE_IMAGE}} AS base\nWORKDIR /app\n{{COPY_FILES}}\nCMD x\n"
