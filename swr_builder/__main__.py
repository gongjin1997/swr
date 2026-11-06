"""启动入口：python -m swr_builder -c config.yaml"""
import argparse
import logging
import tempfile

import uvicorn

from .config import load_settings
from .main import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="SWR 镜像构建平台")
    parser.add_argument("-c", "--config", help="配置文件路径，默认 ./config.yaml 或环境变量 BUILDER_CONFIG")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = load_settings(args.config)
    # 大文件上传先落在数据目录，避免 /tmp 空间不足
    tmp_dir = settings.data_path / "tmp"
    settings.data_path.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    tempfile.tempdir = str(tmp_dir)
    # 只能单进程运行：任务队列和仓库锁在进程内存中
    uvicorn.run(create_app(settings), host=args.host or settings.server.host,
                port=args.port or settings.server.port, log_level="info")


if __name__ == "__main__":
    main()
