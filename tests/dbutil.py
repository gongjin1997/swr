"""测试用的 Store 构造：连接 conftest 为当前测试创建的数据库。"""
import os

from swr_builder.store import Store


def new_store(**kwargs) -> Store:
    return Store(os.environ["BUILDER_DATABASE_URL"], driver=os.environ.get("BUILDER_DATABASE_DRIVER", "psycopg"),
                 **kwargs)
