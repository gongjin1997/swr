"""资源自动申请：根据配置自动创建和初始化资源

功能：
- 根据服务配置自动申请资源
- 执行资源初始化脚本
- 资源父子关系自动建立
- 资源属性模板渲染
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..errors import DomainError
from ..store import Store


class ResourceRequest:
    """资源申请配置"""

    def __init__(
        self,
        resource_type: str,
        name_pattern: str,
        alias: str,
        attributes: Dict[str, Any],
        parent_binding: Optional[str] = None,
        init_script: Optional[str] = None,
    ):
        self.resource_type = resource_type
        self.name_pattern = name_pattern
        self.alias = alias
        self.attributes = attributes
        self.parent_binding = parent_binding
        self.init_script = init_script


def parse_resource_requests(config: Dict[str, Any]) -> List[ResourceRequest]:
    """从配置中解析资源申请

    配置格式（在配置中定义）:
    RESOURCE_REQUESTS: |
      - type: gauss_schema
        name: {service}_schema
        alias: db
        parent: gauss_cluster
        attributes:
          schema: {service}_schema
        init_script: |
          CREATE SCHEMA IF NOT EXISTS {schema};
          GRANT ALL ON SCHEMA {schema} TO {parent.username};
    """
    import yaml

    requests_yaml = config.get("RESOURCE_REQUESTS", "")
    if not requests_yaml:
        return []

    try:
        requests_data = yaml.safe_load(requests_yaml)
        if not isinstance(requests_data, list):
            return []

        requests = []
        for item in requests_data:
            request = ResourceRequest(
                resource_type=item["type"],
                name_pattern=item["name"],
                alias=item["alias"],
                attributes=item.get("attributes", {}),
                parent_binding=item.get("parent"),
                init_script=item.get("init_script"),
            )
            requests.append(request)

        return requests
    except Exception as e:
        raise DomainError(f"解析资源申请配置失败: {e}")


def request_resources(
    store: Store,
    service_id: int,
    requests: List[ResourceRequest],
    context: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """申请资源

    Args:
        store: 数据库连接
        service_id: 服务 ID
        requests: 资源申请列表
        context: 渲染上下文（service、config 等）

    Returns:
        创建的资源绑定列表
    """
    from ..services import resources as resource_service

    service = store.execute_one("SELECT * FROM services WHERE id = %s", service_id)
    if not service:
        raise DomainError(f"服务 {service_id} 不存在")

    bindings = []

    for request in requests:
        # 1. 渲染资源名称
        resource_name = _render_template(request.name_pattern, context)

        # 2. 检查是否已存在
        existing = _find_resource(
            store,
            scope_type="environment",
            scope_id=service["namespace_id"],
            resource_type=request.resource_type,
            name=resource_name,
        )

        if existing:
            # 已存在，直接绑定
            binding = _bind_resource(store, service_id, existing["id"], request.alias)
            bindings.append(binding)
            continue

        # 3. 获取父资源 ID
        parent_resource_id = None
        if request.parent_binding:
            parent_binding = _get_service_binding(store, service_id, request.parent_binding)
            if parent_binding:
                parent_resource_id = parent_binding["resource_id"]
            else:
                raise DomainError(f"父资源绑定 '{request.parent_binding}' 不存在")

        # 4. 渲染资源属性
        rendered_attributes = {}
        for key, value in request.attributes.items():
            if isinstance(value, str):
                rendered_attributes[key] = _render_template(value, context)
            else:
                rendered_attributes[key] = value

        # 5. 创建资源
        import json

        resource = store.execute_one(
            """INSERT INTO resources
               (scope_type, scope_id, type, name, attributes, parent_resource_id, created_by, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
               RETURNING *""",
            "environment",
            service["namespace_id"],
            request.resource_type,
            resource_name,
            json.dumps(rendered_attributes),
            parent_resource_id,
            None,  # TODO: 获取当前用户
        )

        # 6. 创建资源版本
        store.execute(
            """INSERT INTO resource_versions
               (resource_id, number, attributes, note, created_by, created_at)
               VALUES (%s, %s, %s, %s, %s, NOW())""",
            resource["id"],
            1,
            json.dumps(rendered_attributes),
            "自动创建",
            None,
        )

        # 7. 执行初始化脚本
        if request.init_script:
            _execute_init_script(
                store,
                resource,
                request.init_script,
                context,
            )

        # 8. 绑定资源
        binding = _bind_resource(store, service_id, resource["id"], request.alias)
        bindings.append(binding)

    return bindings


def _render_template(template: str, context: Dict[str, Any]) -> str:
    """渲染模板字符串

    支持的占位符:
    - {service} - 服务名称
    - {namespace} - 命名空间
    - {project} - 项目名称
    - {config.KEY} - 配置值
    - {parent.attr} - 父资源属性
    """
    from jinja2 import Template

    # 准备渲染上下文
    render_context = {
        "service": context.get("service", {}).get("name", ""),
        "namespace": context.get("service", {}).get("namespace", ""),
        "project": context.get("project", {}).get("name", ""),
        "config": context.get("config", {}),
    }

    # 渲染
    tmpl = Template(template)
    return tmpl.render(**render_context)


def _find_resource(
    store: Store,
    scope_type: str,
    scope_id: int,
    resource_type: str,
    name: str,
) -> Optional[Dict[str, Any]]:
    """查找资源"""
    return store.execute_one(
        """SELECT * FROM resources
           WHERE scope_type = %s AND scope_id = %s AND type = %s AND name = %s
           LIMIT 1""",
        scope_type,
        scope_id,
        resource_type,
        name,
    )


def _bind_resource(
    store: Store,
    service_id: int,
    resource_id: int,
    alias: str,
) -> Dict[str, Any]:
    """绑定资源到服务"""
    # 检查是否已绑定
    existing = store.execute_one(
        """SELECT * FROM service_resource_bindings
           WHERE service_id = %s AND alias = %s""",
        service_id,
        alias,
    )

    if existing:
        # 已绑定，更新
        return store.execute_one(
            """UPDATE service_resource_bindings
               SET resource_id = %s, updated_at = NOW()
               WHERE id = %s
               RETURNING *""",
            resource_id,
            existing["id"],
        )
    else:
        # 新建绑定
        return store.execute_one(
            """INSERT INTO service_resource_bindings
               (service_id, resource_id, alias, created_at, updated_at)
               VALUES (%s, %s, %s, NOW(), NOW())
               RETURNING *""",
            service_id,
            resource_id,
            alias,
        )


def _get_service_binding(
    store: Store,
    service_id: int,
    alias: str,
) -> Optional[Dict[str, Any]]:
    """获取服务的资源绑定"""
    return store.execute_one(
        """SELECT * FROM service_resource_bindings
           WHERE service_id = %s AND alias = %s""",
        service_id,
        alias,
    )


def _execute_init_script(
    store: Store,
    resource: Dict[str, Any],
    script_template: str,
    context: Dict[str, Any],
) -> None:
    """执行资源初始化脚本

    目前仅支持 GaussDB Schema 的初始化
    未来可扩展支持其他资源类型
    """
    import json

    resource_type = resource["type"]
    attributes = json.loads(resource["attributes"]) if isinstance(resource["attributes"], str) else resource["attributes"]

    if resource_type == "gauss_schema":
        _execute_gauss_schema_init(store, resource, attributes, script_template, context)
    else:
        # 其他资源类型暂不支持初始化脚本
        pass


def _execute_gauss_schema_init(
    store: Store,
    resource: Dict[str, Any],
    attributes: Dict[str, Any],
    script_template: str,
    context: Dict[str, Any],
) -> None:
    """执行 GaussDB Schema 初始化脚本"""
    # 获取父资源（GaussDB）
    if not resource.get("parent_resource_id"):
        raise DomainError("GaussDB Schema 必须有父资源（GaussDB）")

    parent = store.execute_one(
        "SELECT * FROM resources WHERE id = %s",
        resource["parent_resource_id"]
    )

    if not parent:
        raise DomainError("父资源（GaussDB）不存在")

    import json
    parent_attributes = json.loads(parent["attributes"]) if isinstance(parent["attributes"], str) else parent["attributes"]

    # 渲染脚本
    from jinja2 import Template

    render_context = {
        "schema": attributes.get("schema"),
        "parent": parent_attributes,
    }

    script = Template(script_template).render(**render_context)

    # 执行脚本（使用 psycopg2）
    try:
        import psycopg2

        conn = psycopg2.connect(
            host=parent_attributes["host"],
            port=parent_attributes["port"],
            user=parent_attributes["username"],
            password=parent_attributes["password"],
            database=parent_attributes["database"],
        )

        cursor = conn.cursor()
        cursor.execute(script)
        conn.commit()
        cursor.close()
        conn.close()

    except Exception as e:
        raise DomainError(f"执行初始化脚本失败: {e}")
