"""测试数据库：每个测试使用一个新建的 PostgreSQL（或 PG 兼容模式的 GaussDB / openGauss）数据库。

需要环境变量 SWR_TEST_DATABASE_URL，指向一个有 CREATEDB 权限的账号，例如
postgresql://postgres@127.0.0.1:5432/postgres。用华为 GaussDB 驱动测试时另设 SWR_TEST_DATABASE_DRIVER=gaussdb。
测试期间 BUILDER_DATABASE_URL 指向本测试的数据库，create_app() 与 tests.dbutil.new_store() 都使用它。
"""
import os
import uuid
from urllib.parse import urlsplit, urlunsplit

import pytest

from swr_builder.store import close_all
from swr_builder.store.db import load_driver, normalize_url

ADMIN_URL = os.environ.get("SWR_TEST_DATABASE_URL", "")
DRIVER = os.environ.get("SWR_TEST_DATABASE_DRIVER", "psycopg")


def _database_url(url: str, name: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, "/" + name, parts.query, parts.fragment))


def _is_gauss(conn) -> bool:
    version = (conn.execute("SELECT version()").fetchone()[0] or "").lower()
    return "opengauss" in version or "gaussdb" in version


@pytest.fixture(autouse=True)
def database_url(monkeypatch):
    if not ADMIN_URL:
        pytest.fail("测试需要 PostgreSQL：请设置 SWR_TEST_DATABASE_URL（见 docs/开发文档.md「测试」）")
    driver = load_driver(DRIVER)
    name = "swr_test_" + uuid.uuid4().hex[:16]
    admin = driver.connect(normalize_url(ADMIN_URL), autocommit=True)
    try:
        gauss = _is_gauss(admin)
        if gauss:
            admin.execute(f"CREATE DATABASE {name} ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C' TEMPLATE template0 "
                          "DBCOMPATIBILITY 'PG'")
        else:
            admin.execute(f"CREATE DATABASE {name}")
    finally:
        admin.close()
    url = _database_url(ADMIN_URL, name)
    monkeypatch.setenv("BUILDER_DATABASE_URL", url)
    monkeypatch.setenv("BUILDER_DATABASE_DRIVER", DRIVER)
    yield url
    close_all()
    admin = driver.connect(normalize_url(ADMIN_URL), autocommit=True)
    try:
        if gauss:
            admin.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s", (name,))
            admin.execute(f"DROP DATABASE IF EXISTS {name}")
        else:
            admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
    except Exception:  # noqa: BLE001 - 清理失败不影响测试结果，临时库随测试环境一起销毁
        pass
    finally:
        admin.close()
