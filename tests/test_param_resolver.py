"""参数解析器单元测试（基于 XAC getParams 逻辑）。

测试优先级、来源追踪、覆盖检测等核心功能。
"""
import pytest

from swr_builder.core.domain import (
    ConfigLayer,
    ConfigSet,
    DeploymentUnit,
    Resource,
    ResourceBinding,
)
from swr_builder.core.param_resolver import ParamResolver


def test_priority_order():
    """测试优先级顺序：feature < resource < config < du_attr < artifact < cert < external"""
    du = DeploymentUnit(
        id=1,
        name="test-service",
        config_layers=[
            ConfigLayer(
                layer="feature",
                scope="platform",
                config_sets=[
                    ConfigSet(
                        id=1,
                        kind="env",
                        name="feature-flags",
                        version=1,
                        entries={"FEATURE_A": {"key": "FEATURE_A", "value": "false", "locked": False}},
                    )
                ],
            ),
            ConfigLayer(
                layer="config",
                scope="environment",
                config_sets=[
                    ConfigSet(
                        id=2,
                        kind="env",
                        name="env-config",
                        version=1,
                        entries={"FEATURE_A": {"key": "FEATURE_A", "value": "true", "locked": False}},
                    )
                ],
            ),
        ],
        resource_bindings=[],
        attributes={},
    )

    resolver = ParamResolver()
    result = resolver.resolve(du)

    # config 层优先级高于 feature 层
    assert result["params"]["FEATURE_A"]["value"] == "true"
    assert result["params"]["FEATURE_A"]["source_layer"] == "config"
    assert result["params"]["FEATURE_A"]["overridden"] == False
    assert result["sources"]["FEATURE_A"]["overridden_layers"] == ["feature"]


def test_locked_parameter():
    """测试锁定参数不被覆盖"""
    du = DeploymentUnit(
        id=1,
        name="test-service",
        config_layers=[
            ConfigLayer(
                layer="config",
                scope="platform",
                config_sets=[
                    ConfigSet(
                        id=1,
                        kind="env",
                        name="platform-config",
                        version=1,
                        entries={"DB_HOST": {"key": "DB_HOST", "value": "prod.db", "locked": True}},
                    )
                ],
            ),
            ConfigLayer(
                layer="config",
                scope="du",
                config_sets=[
                    ConfigSet(
                        id=2,
                        kind="env",
                        name="service-config",
                        version=1,
                        entries={"DB_HOST": {"key": "DB_HOST", "value": "test.db", "locked": False}},
                    )
                ],
            ),
        ],
        resource_bindings=[],
        attributes={},
    )

    resolver = ParamResolver()
    result = resolver.resolve(du)

    # 锁定参数不被覆盖
    assert result["params"]["DB_HOST"]["value"] == "prod.db"
    assert result["params"]["DB_HOST"]["locked"] == True
    assert "锁定" in result["warnings"][0]


def test_resource_export():
    """测试资源导出到参数"""
    resource = Resource(
        id=1,
        type="redis",
        name="my-redis",
        scope_type="environment",
        scope_id=1,
        attributes={"host": "redis.example.com", "port": 6379, "database": 0},
        version=1,
    )

    du = DeploymentUnit(
        id=1,
        name="test-service",
        config_layers=[],
        resource_bindings=[
            ResourceBinding(
                du_id=1,
                resource_id=1,
                alias="",
                resource=resource,
            )
        ],
        attributes={},
    )

    resolver = ParamResolver()
    result = resolver.resolve(du)

    # 资源导出的参数
    assert "spring.redis.host" in result["params"]
    assert result["params"]["spring.redis.host"]["value"] == "redis.example.com"
    assert result["params"]["spring.redis.port"]["value"] == "6379"
    assert result["params"]["spring.redis.database"]["value"] == "0"


def test_du_attributes_override():
    """测试 DU 属性覆盖配置"""
    du = DeploymentUnit(
        id=1,
        name="test-service",
        config_layers=[
            ConfigLayer(
                layer="config",
                scope="environment",
                config_sets=[
                    ConfigSet(
                        id=1,
                        kind="env",
                        name="env-config",
                        version=1,
                        entries={"REPLICAS": {"key": "REPLICAS", "value": "2", "locked": False}},
                    )
                ],
            ),
        ],
        resource_bindings=[],
        attributes={"replicas": 5},
    )

    resolver = ParamResolver()
    result = resolver.resolve(du)

    # DU 属性优先级高于 config 层
    assert result["params"]["replicas"]["value"] == 5
    assert result["params"]["replicas"]["source_layer"] == "du_attr"


def test_multiple_config_sets_same_layer():
    """测试同一层的多个配置集按 ID 排序"""
    du = DeploymentUnit(
        id=1,
        name="test-service",
        config_layers=[
            ConfigLayer(
                layer="config",
                scope="environment",
                config_sets=[
                    ConfigSet(
                        id=1,
                        kind="env",
                        name="base-config",
                        version=1,
                        entries={"KEY": {"key": "KEY", "value": "v1", "locked": False}},
                    ),
                    ConfigSet(
                        id=2,
                        kind="env",
                        name="override-config",
                        version=1,
                        entries={"KEY": {"key": "KEY", "value": "v2", "locked": False}},
                    ),
                ],
            ),
        ],
        resource_bindings=[],
        attributes={},
    )

    resolver = ParamResolver()
    result = resolver.resolve(du)

    # 后面的配置集覆盖前面的（ID 大的覆盖 ID 小的）
    assert result["params"]["KEY"]["value"] == "v2"
    assert result["sources"]["KEY"]["source_id"] == 2


def test_source_tracking():
    """测试完整的来源追踪"""
    du = DeploymentUnit(
        id=1,
        name="test-service",
        config_layers=[
            ConfigLayer(
                layer="config",
                scope="platform",
                config_sets=[
                    ConfigSet(
                        id=1,
                        kind="env",
                        name="platform-defaults",
                        version=3,
                        entries={"LOG_LEVEL": {"key": "LOG_LEVEL", "value": "INFO", "locked": False}},
                    )
                ],
            ),
        ],
        resource_bindings=[],
        attributes={},
    )

    resolver = ParamResolver()
    result = resolver.resolve(du)

    source = result["sources"]["LOG_LEVEL"]
    assert source["source_layer"] == "config"
    assert source["scope"] == "platform"
    assert source["source_type"] == "config"
    assert source["source_id"] == 1
    assert source["source_name"] == "platform-defaults"
    assert source["version"] == 3
    assert source["locked"] == False
    assert source["overridden"] == False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
