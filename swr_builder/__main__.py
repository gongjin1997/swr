"""启动入口。

    python -m swr_builder -c config.yaml                              启动平台
    python -m swr_builder db upgrade -c config.yaml                   执行数据库迁移
    python -m swr_builder db check -c config.yaml                     检查数据库连接与版本
    python -m swr_builder db import-sqlite --from data/builder.db     导入 v1.x 的 SQLite 数据
"""
import argparse
import logging
import sys
import tempfile
from typing import List, Optional

import uvicorn

from .config import load_settings


def _db(argv: List[str]) -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-c", "--config", help="配置文件路径，默认 ./config.yaml 或环境变量 BUILDER_CONFIG")
    parser = argparse.ArgumentParser(prog="python -m swr_builder db", description="数据库维护", parents=[common])
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("upgrade", parents=[common], help="执行未应用的迁移")
    sub.add_parser("check", parents=[common], help="检查连接、数据库类型与迁移版本")
    imp = sub.add_parser("import-sqlite", parents=[common], help="把 v1.x 的 SQLite 数据库导入到空的 PostgreSQL / GaussDB")
    imp.add_argument("--from", dest="source", required=True, help="SQLite 数据库文件，例如 data/builder.db")
    args = parser.parse_args(argv)

    from .app import open_store
    from .store.importer import describe, import_sqlite
    from .store.migrations import schema_versions

    settings = load_settings(args.config)
    store = open_store(settings, migrate=True)
    try:
        if args.action == "check":
            print(f"数据库：{store.url}\n类型：{store.flavor}\n版本：{store.server_version}\n"
                  f"已应用迁移：{schema_versions(store)}")
        elif args.action == "import-sqlite":
            print(describe(import_sqlite(store, args.source), store.url))
        else:
            print(f"已应用迁移：{schema_versions(store)}")
    finally:
        store.close()
    return 0


def main(argv: Optional[List[str]] = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if argv and argv[0] == "db":
        try:
            raise SystemExit(_db(argv[1:]))
        except (RuntimeError, ValueError, FileNotFoundError) as exc:
            print(f"错误：{exc}", file=sys.stderr)
            raise SystemExit(1)

    parser = argparse.ArgumentParser(description="SWR 镜像构建平台")
    parser.add_argument("-c", "--config", help="配置文件路径，默认 ./config.yaml 或环境变量 BUILDER_CONFIG")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    args = parser.parse_args(argv)
    settings = load_settings(args.config)
    # 大文件上传先落在数据目录，避免 /tmp 空间不足
    tmp_dir = settings.data_path / "tmp"
    settings.data_path.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    tempfile.tempdir = str(tmp_dir)
    from .main import create_app

    # 只能单进程运行：构建队列和仓库锁在进程内存中
    uvicorn.run(create_app(settings), host=args.host or settings.server.host,
                port=args.port or settings.server.port, log_level="info")


if __name__ == "__main__":
    main()
