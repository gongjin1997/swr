"""资源管理、绑定与导出的集成测试。"""
import pytest

from swr_builder.delivery import resources as resource_types
from swr_builder.errors import Conflict, Forbidden, NotFound
from swr_builder.security import SecretBox
from tests.dbutil import new_store


def test_resource_types_registry():
    """测试资源类型注册"""
    types = list(resource_types.RESOURCE_TYPES.keys())
    assert len(types) == 9
    assert "gauss_db" in types
    assert "gauss_schema" in types
    assert "gauss_user" in types
    assert "redis" in types


def test_validate_attributes():
    """测试属性校验"""
    # 正常情况
    attrs = {"host": "db.example", "port": 5432}
    validated = resource_types.validate_attributes("gauss_db", attrs, None)
    assert validated == {"host": "db.example", "port": 5432}

    # 使用默认值
    attrs = {"host": "db.example"}
    validated = resource_types.validate_attributes("gauss_db", attrs, None)
    assert validated["port"] == 5432

    # 必填字段缺失
    with pytest.raises(ValueError, match="必填项"):
        resource_types.validate_attributes("gauss_db", {}, None)

    # 类型错误
    with pytest.raises(ValueError, match="必须是整数"):
        resource_types.validate_attributes("gauss_db", {"host": "db.example", "port": "abc"}, None)


def test_secret_attributes():
    """测试敏感属性识别"""
    secrets = resource_types.secret_attributes("gauss_user")
    assert "password" in secrets
    assert "user" not in secrets

    secrets = resource_types.secret_attributes("redis")
    assert "password" in secrets


def test_mask_secrets():
    """测试敏感属性屏蔽"""
    attrs = {"user": "test_user", "password": "secret123"}
    masked = resource_types.mask_secrets("gauss_user", attrs)
    assert masked["user"] == "test_user"
    assert masked["password"] == "******"


def test_compute_exports():
    """测试导出计算"""
    chain = [
        {"type": "gauss_db", "attributes": {"host": "db.example", "port": 5432}},
    ]
    exports = resource_types.compute_exports(chain)
    assert ("DB_HOST", "db.example") in exports["env"]
    assert ("DB_PORT", "5432") in exports["env"]


def test_compute_exports_with_parent_chain():
    """测试父链导出计算"""
    chain = [
        {"type": "gauss_user", "attributes": {"user": "app_user", "password": "secret"}},
        {"type": "gauss_schema", "attributes": {"database": "app_db", "schema": "public"}},
        {"type": "gauss_db", "attributes": {"host": "db.example", "port": 5432}},
    ]
    exports = resource_types.compute_exports(chain)

    # DB 层导出
    assert ("DB_HOST", "db.example") in exports["env"]
    assert ("DB_PORT", "5432") in exports["env"]

    # Schema 层导出
    assert ("DB_NAME", "app_db") in exports["env"]
    assert ("DB_SCHEMA", "public") in exports["env"]

    # User 层导出
    assert ("DB_USER", "app_user") in exports["env"]
    assert ("DB_PASSWORD", "secret") in exports["secret_env"]
    assert any("DB_URL" in k for k, v in exports["env"])


def test_resource_crud():
    """测试资源 CRUD"""
    from swr_builder.access import load_principal

    store = new_store()
    box = SecretBox(b"test" * 8)

    # 创建平台管理员
    user = store.create_user(username="admin", display_name="Admin", email="admin@example.com",
                            password_hash="hash", role="platform_admin", enabled=1, must_change_password=0)
    me = load_principal(store, user)

    # 创建资源
    attrs = {"host": "db.example", "port": 5432}
    validated = resource_types.validate_attributes("gauss_db", attrs, box)
    checksum = resource_types.checksum(validated)

    resource_id = store.create_resource("platform", 0, "gauss_db", "test-db", "测试数据库",
                                       validated, checksum, "初始版本", user["id"])
    assert resource_id > 0

    # 读取资源
    resource = store.get_resource(resource_id)
    assert resource is not None
    assert resource["name"] == "test-db"
    assert resource["type"] == "gauss_db"
    assert resource["version"] == 1

    # 更新资源（创建新版本）
    new_attrs = {"host": "db2.example", "port": 5433}
    validated = resource_types.validate_attributes("gauss_db", new_attrs, box)
    checksum = resource_types.checksum(validated)

    new_version = store.add_resource_version(resource_id, 1, validated, checksum, "更新主机", user["id"])
    assert new_version == 2

    # 版本列表
    versions = store.list_resource_versions(resource_id)
    assert len(versions) == 2
    assert versions[0]["number"] == 2
    assert versions[1]["number"] == 1

    # 删除资源
    assert store.delete_resource(resource_id)
    assert store.get_resource(resource_id) is None


def test_service_resource_binding():
    """测试服务资源绑定"""
    from swr_builder.access import load_principal

    store = new_store()
    box = SecretBox(b"test" * 8)

    # 创建用户和基础数据
    user = store.create_user(username="admin", display_name="Admin", email="admin@example.com",
                            password_hash="hash", role="platform_admin", enabled=1, must_change_password=0)
    me = load_principal(store, user)

    project = store.create_project("test-project", "测试项目", [])
    project_id = project["id"]

    app = store.create_app({"slug": "test-app", "name": "测试应用", "repo": "harbor.example/test",
                           "description": "测试", "configs": [], "secrets": []}, project_id)
    app_id = app["id"]

    cluster_id = store.create_cluster({"name": "test-cluster", "title": "测试集群",
                                      "server": "https://k8s.example:6443", "auth_type": "sa_token",
                                      "credential_encrypted": "{}"})
    namespace_id = store.create_namespace(cluster_id, "test-ns", "dev", {}, project_id)

    service_id = store.create_service("test-service", project_id, namespace_id, app_id, {"mode": "managed"}, user["id"])

    # 创建资源
    attrs = {"host": "redis.example", "port": 6379, "password": "secret"}
    validated = resource_types.validate_attributes("redis", attrs, box)
    checksum = resource_types.checksum(validated)
    resource_id = store.create_resource("environment", namespace_id, "redis", "cache-redis", "缓存",
                                       validated, checksum, "初始版本", user["id"])

    # 绑定资源
    store.create_service_resource_binding(service_id, resource_id, "cache")

    # 查询绑定
    bindings = store.list_service_resource_bindings(service_id)
    assert len(bindings) == 1
    assert bindings[0]["alias"] == "cache"
    assert bindings[0]["resource_id"] == resource_id

    # 删除绑定
    assert store.delete_service_resource_binding(service_id, "cache")
    bindings = store.list_service_resource_bindings(service_id)
    assert len(bindings) == 0


def test_resource_binding_prevents_deletion():
    """测试有绑定的资源不能删除"""
    from swr_builder.access import load_principal

    store = new_store()
    box = SecretBox(b"test" * 8)

    user = store.create_user(username="admin", display_name="Admin", email="admin@example.com",
                            password_hash="hash", role="platform_admin", enabled=1, must_change_password=0)
    me = load_principal(store, user)

    project = store.create_project("test-project", "测试项目", [])
    project_id = project["id"]

    app = store.create_app({"slug": "test-app", "name": "测试应用", "repo": "harbor.example/test",
                           "description": "测试", "configs": [], "secrets": []}, project_id)
    app_id = app["id"]

    cluster_id = store.create_cluster({"name": "test-cluster", "title": "测试集群",
                                      "server": "https://k8s.example:6443", "auth_type": "sa_token",
                                      "credential_encrypted": "{}"})
    namespace_id = store.create_namespace(cluster_id, "test-ns", "dev", {}, project_id)
    service_id = store.create_service("test-service", project_id, namespace_id, app_id, {"mode": "managed"}, user["id"])

    attrs = {"host": "redis.example", "port": 6379}
    validated = resource_types.validate_attributes("redis", attrs, box)
    checksum = resource_types.checksum(validated)
    resource_id = store.create_resource("environment", namespace_id, "redis", "cache-redis", "缓存",
                                       validated, checksum, "初始版本", user["id"])

    store.create_service_resource_binding(service_id, resource_id, "cache")

    # 删除应该失败（FK RESTRICT）
    with pytest.raises(Exception):  # IntegrityError 或数据库错误
        store.delete_resource(resource_id)

    # 解绑后可以删除
    store.delete_service_resource_binding(service_id, "cache")
    assert store.delete_resource(resource_id)


def test_preset_crud():
    """测试环境预置 CRUD"""
    store = new_store()

    profile_template = {"naming": {"deployment": "{base}-app"}}
    default_config_sets = [{"kind": "env", "name": "env", "entries": [{"key": "ENV", "value": "test"}]}]
    required_resource_types = ["gauss_db", "redis"]
    required_params = [{"key": "DB_HOST", "label": "数据库地址", "required": True}]

    preset_id = store.create_preset("dev-preset", "开发环境预置", profile_template, default_config_sets,
                                   required_resource_types, required_params, 1)
    assert preset_id > 0

    preset = store.get_preset(preset_id)
    assert preset is not None
    assert preset["name"] == "dev-preset"
    assert len(preset["required_resource_types"]) == 2

    # 更新
    assert store.update_preset(preset_id, "dev-preset-v2", "开发环境预置 v2", profile_template,
                              default_config_sets, required_resource_types, required_params, 1)

    preset = store.get_preset(preset_id)
    assert preset["name"] == "dev-preset-v2"

    # 删除
    assert store.delete_preset(preset_id)
    assert store.get_preset(preset_id) is None


def test_apply_preset_to_environment():
    """测试应用预置到环境"""
    store = new_store()
    box = SecretBox(b"test" * 8)

    user = store.create_user(username="admin", display_name="Admin", email="admin@example.com",
                            password_hash="hash", role="platform_admin", enabled=1, must_change_password=0)
    project = store.create_project("test-project", "测试项目", [])
    project_id = project["id"]

    cluster_id = store.create_cluster({"name": "test-cluster", "title": "测试集群",
                                      "server": "https://k8s.example:6443", "auth_type": "sa_token",
                                      "credential_encrypted": "{}"})
    namespace_id = store.create_namespace(cluster_id, "test-ns", "dev", {}, project_id)

    # 创建预置
    profile_template = {"naming": {"deployment": "{base}-app"}}
    default_config_sets = [
        {"kind": "env", "name": "common-env", "entries": [{"key": "LOG_LEVEL", "value": "debug"}]}
    ]
    required_resource_types = ["redis"]
    required_params = [{"key": "REDIS_HOST", "label": "Redis 地址"}]

    preset = {
        "id": 1,
        "profile_template": profile_template,
        "default_config_sets": default_config_sets,
        "required_resource_types": required_resource_types,
        "required_params": required_params,
    }

    # 应用预置
    store.apply_preset_to_environment(namespace_id, preset, user["id"])

    # 验证环境档案已更新
    profile = store.get_environment_profile(namespace_id)
    assert profile is not None
    assert profile["version"] == 1
    assert "required_params" in profile

    import json
    required_params = json.loads(profile["required_params"]) if isinstance(profile["required_params"], str) else profile["required_params"]
    assert len(required_params) == 1

    # 验证配置集已创建
    config_sets = store.list_config_sets([("environment", namespace_id)])
    assert len(config_sets) == 1
    assert config_sets[0]["name"] == "common-env"
    assert config_sets[0]["kind"] == "env"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
