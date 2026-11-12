"""资源推断引擎单元测试。

测试从 K8s YAML 推断资源类型的核心逻辑。
"""
import pytest

from swr_builder.core.resource_inference import ResourceInferenceEngine


def test_infer_from_deployment_env():
    """测试从 Deployment 的环境变量推断资源"""
    deployment = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": "test-app"},
        "spec": {
            "template": {
                "spec": {
                    "containers": [
                        {
                            "name": "main",
                            "env": [
                                {"name": "REDIS_HOST", "value": "redis.example.com"},
                                {"name": "REDIS_PORT", "value": "6379"},
                                {"name": "KAFKA_BROKERS", "value": "kafka:9092"},
                            ],
                        }
                    ]
                }
            }
        },
    }

    engine = ResourceInferenceEngine()
    inferences = engine.infer_from_deployment(deployment)

    # 应该推断出 redis 和 kafka
    types = {inf["type"] for inf in inferences}
    assert "redis" in types
    assert "kafka" in types

    redis_inf = next(inf for inf in inferences if inf["type"] == "redis")
    assert "REDIS_HOST" in redis_inf["evidence"]
    assert "REDIS_PORT" in redis_inf["evidence"]
    assert redis_inf["confidence"] >= 0.7


def test_infer_from_configmap():
    """测试从 ConfigMap 推断资源"""
    configmap = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": "app-config"},
        "data": {
            "spring.datasource.gauss.host": "gaussdb.example.com",
            "spring.datasource.gauss.port": "5432",
            "spring.datasource.gauss.database": "mydb",
            "spring.redis.host": "redis.example.com",
            "spring.redis.port": "6379",
        },
    }

    engine = ResourceInferenceEngine()
    inferences = engine.infer_from_configmap(configmap)

    types = {inf["type"] for inf in inferences}
    assert "gauss_db" in types
    assert "redis" in types

    gauss_inf = next(inf for inf in inferences if inf["type"] == "gauss_db")
    assert gauss_inf["confidence"] >= 0.7
    assert "spring.datasource.gauss.host" in gauss_inf["evidence"]


def test_suggest_attributes():
    """测试推荐的资源属性"""
    engine = ResourceInferenceEngine()

    # Redis
    suggestions = engine._suggest_attributes("redis", {"REDIS_HOST", "REDIS_PORT"})
    assert "host" in suggestions
    assert suggestions["port"] == 6379

    # GaussDB
    suggestions = engine._suggest_attributes("gauss_db", {"spring.datasource.gauss.host"})
    assert "host" in suggestions
    assert suggestions["port"] == 5432


def test_confidence_calculation():
    """测试置信度计算"""
    engine = ResourceInferenceEngine()

    # 1 个证据：置信度 0.5
    assert engine._calculate_confidence("redis", {"REDIS_HOST"}) == 0.5

    # 2 个证据：置信度 0.7
    assert engine._calculate_confidence("redis", {"REDIS_HOST", "REDIS_PORT"}) == 0.7

    # 3+ 个证据：置信度 0.9
    assert engine._calculate_confidence(
        "gauss_db",
        {"spring.datasource.gauss.host", "spring.datasource.gauss.port", "spring.datasource.gauss.database"},
    ) == 0.9


def test_analyze_namespace_yaml():
    """测试分析完整的命名空间 YAML"""
    from swr_builder.core.resource_inference import analyze_namespace_yaml

    objects = [
        {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": "app1", "namespace": "test"},
            "spec": {
                "template": {
                    "spec": {
                        "containers": [
                            {
                                "name": "main",
                                "env": [
                                    {"name": "REDIS_HOST", "value": "redis"},
                                ],
                            }
                        ]
                    }
                }
            },
        },
        {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": "config1", "namespace": "test"},
            "data": {
                "spring.kafka.bootstrap-servers": "kafka:9092",
            },
        },
    ]

    result = analyze_namespace_yaml(objects)

    assert len(result["deployments"]) == 1
    assert result["deployments"][0]["name"] == "app1"
    assert any(inf["type"] == "redis" for inf in result["deployments"][0]["inferences"])

    assert len(result["configmaps"]) == 1
    assert result["configmaps"][0]["name"] == "config1"

    assert "redis" in result["summary"]
    assert "kafka" in result["summary"]


def test_no_inference_for_unknown_patterns():
    """测试不匹配任何模式时不推断"""
    deployment = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": "test-app"},
        "spec": {
            "template": {
                "spec": {
                    "containers": [
                        {
                            "name": "main",
                            "env": [
                                {"name": "UNKNOWN_VAR", "value": "value"},
                            ],
                        }
                    ]
                }
            }
        },
    }

    engine = ResourceInferenceEngine()
    inferences = engine.infer_from_deployment(deployment)

    assert len(inferences) == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
