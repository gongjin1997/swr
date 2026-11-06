"""SQLite 存储。

`Store` 由按领域拆分的 mixin 组合而成，对外方法签名与 v1.2 保持一致。
所有方法都通过 `Database._db()` 取进程内锁并使用独立连接；锁不可重入，mixin 方法内部不要嵌套调用 `_db()`。
"""
from .accounts import LEGACY_ROLE_BINDINGS, AccountsMixin
from .apps import AppsMixin
from .base import Database
from .bindings import BindingsMixin
from .clusters import ClustersMixin
from .deployments import DeploymentsMixin
from .jobs import JobsMixin
from .projects import ProjectsMixin
from .schema import DEPLOYMENT_FIELDS, JOB_FIELDS, SCHEMA, SCHEMA_EXT, USER_FIELDS, utcnow
from .services import ServicesMixin
from .system import SystemMixin


class Store(AppsMixin, ProjectsMixin, ClustersMixin, BindingsMixin, ServicesMixin, JobsMixin, AccountsMixin,
            SystemMixin, DeploymentsMixin, Database):
    pass


__all__ = ["Store", "SCHEMA", "SCHEMA_EXT", "USER_FIELDS", "JOB_FIELDS", "DEPLOYMENT_FIELDS", "LEGACY_ROLE_BINDINGS",
           "utcnow"]
