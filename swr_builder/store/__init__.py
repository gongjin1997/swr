"""PostgreSQL / GaussDB 存储。

`Store` 由按领域拆分的 mixin 组合而成。每个方法通过一次 `Database._db()` 完成一个事务；
同一线程内不能嵌套调用 `_db()`，需要组合多个操作时在同一个事务里完成。
"""
from .accounts import LEGACY_ROLE_BINDINGS, AccountsMixin
from .apps import AppsMixin
from .bindings import BindingsMixin
from .clusters import ClustersMixin
from .configs import ConfigMixin
from .db import Database, IntegrityViolation, close_all, redact_url
from .deployments import DeploymentsMixin
from .jobs import JobsMixin
from .projects import ProjectsMixin
from .roles import RolesMixin
from .schema import DEPLOYMENT_FIELDS, JOB_FIELDS, USER_FIELDS, utcnow
from .services import ServicesMixin
from .system import SystemMixin


class Store(RolesMixin, AppsMixin, ProjectsMixin, ClustersMixin, ConfigMixin, BindingsMixin, ServicesMixin, JobsMixin,
            AccountsMixin, SystemMixin, DeploymentsMixin, Database):
    pass


__all__ = ["Store", "Database", "IntegrityViolation", "USER_FIELDS", "JOB_FIELDS", "DEPLOYMENT_FIELDS",
           "LEGACY_ROLE_BINDINGS", "close_all", "redact_url", "utcnow"]
