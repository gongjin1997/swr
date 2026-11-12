"""启动入口。

    python -m swr_builder -c config.yaml                              启动平台
    python -m swr_builder db upgrade -c config.yaml                   执行数据库迁移
    python -m swr_builder db check -c config.yaml                     检查数据库连接与版本
    python -m swr_builder db import-sqlite --from data/builder.db     导入 v1.x 的 SQLite 数据
    python -m swr_builder db apply-sql --dir sql --patch 2.0.1        执行补丁包 / 铺底数据中的 SQL 脚本（--check 只校验）
    python -m swr_builder db status --json                            迁移版本、执行中的任务、已执行的脚本
"""
import argparse
import logging
import sys
import tempfile
from typing import List, Optional

import uvicorn

from .config import load_settings


def db_parser() -> argparse.ArgumentParser:
    """`db` 子命令的参数定义；离线部署控制器的测试也用它核对传给一次性容器的参数。"""
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-c", "--config", help="配置文件路径，默认 ./config.yaml 或环境变量 BUILDER_CONFIG")
    parser = argparse.ArgumentParser(prog="python -m swr_builder db", description="数据库维护", parents=[common])
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("upgrade", parents=[common], help="执行未应用的迁移")
    sub.add_parser("check", parents=[common], help="检查连接、数据库类型与迁移版本")
    imp = sub.add_parser("import-sqlite", parents=[common], help="把 v1.x 的 SQLite 数据库导入到空的 PostgreSQL / GaussDB")
    imp.add_argument("--from", dest="source", required=True, help="SQLite 数据库文件，例如 data/builder.db")
    sql = sub.add_parser("apply-sql", parents=[common], help="执行目录中的 SQL 脚本（ddl/ → dml/ → *.sql），每个脚本只执行一次")
    sql.add_argument("--dir", required=True, help="脚本目录，例如补丁包的 sql/")
    sql.add_argument("--patch", required=True, help="补丁版本或铺底数据名称，用于记录与去重")
    sql.add_argument("--json", action="store_true", help="以 JSON 输出执行结果")
    sql.add_argument("--check", action="store_true",
                     help="只校验脚本并列出将执行 / 跳过的脚本，不执行脚本，也不执行迁移")
    state = sub.add_parser("status", parents=[common], help="迁移版本、执行中的构建与发布数量、已执行的脚本数量（不执行迁移）")
    state.add_argument("--json", action="store_true")
    return parser


def _db(argv: List[str]) -> int:
    args = db_parser().parse_args(argv)

    import json
    from pathlib import Path

    from .app import open_store
    from .store import sqlscripts
    from .store.importer import describe, import_sqlite
    from .store.migrations import schema_versions

    settings = load_settings(args.config)
    read_only = args.action == "status" or (args.action == "apply-sql" and args.check)
    store = open_store(settings, migrate=not read_only)
    try:
        if args.action == "check":
            print(f"数据库：{store.url}\n类型：{store.flavor}\n版本：{store.server_version}\n"
                  f"已应用迁移：{schema_versions(store)}")
        elif args.action == "import-sqlite":
            print(describe(import_sqlite(store, args.source), store.url))
        elif args.action == "apply-sql" and args.check:
            plan = sqlscripts.plan_scripts(store, args.patch, sqlscripts.discover(Path(args.dir)))
            if args.json:
                print(json.dumps({"patch": args.patch, "check": True, "scripts": plan}, ensure_ascii=False))
            else:
                for item in plan:
                    print(f"{'将执行' if item['status'] == 'pending' else '已执行，跳过'}  {item['path']}")
                print(f"{args.patch}：{sum(1 for i in plan if i['status'] == 'pending')} 个脚本将执行，校验通过")
        elif args.action == "apply-sql":
            scripts = sqlscripts.discover(Path(args.dir))
            log = (lambda _message: None) if args.json else print
            results = sqlscripts.apply_scripts(store, args.patch, scripts, log=log)
            if args.json:
                print(json.dumps({"patch": args.patch, "scripts": results}, ensure_ascii=False))
            else:
                applied = sum(1 for r in results if r["status"] == "applied")
                print(f"{args.patch}：执行 {applied} 个脚本，跳过 {len(results) - applied} 个")
        elif args.action == "status":
            report = sqlscripts.status(store)
            if args.json:
                print(json.dumps(report, ensure_ascii=False))
            else:
                print(f"数据库：{store.url}（{store.flavor}）\n已应用迁移：{report['migrations']}，"
                      f"待执行：{report['pending']}，本程序不认识：{report['unknown']}\n"
                      f"执行中的构建：{report['active']['jobs']}，执行中的发布：{report['active']['deployments']}\n"
                      f"已执行的补丁脚本：{report['scripts']}")
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
