"""现场认证诊断：python -m swr_builder.cloud -c config.yaml；不输出 Token。"""
import argparse
import asyncio
import json
import os
from ..config import load_settings
from ..security import SecretBox
from ..sysconfig import SysConfig
from .hcs import HCSClient


async def check(path):
    from ..app import open_store

    settings = load_settings(path)
    store = open_store(settings, migrate=False)
    box = SecretBox.load(settings.data_path / 'secret.key',os.environ.get('BUILDER_SECRET_KEY',''))
    SysConfig(settings,store,box).load()
    client = HCSClient(settings.hcs)
    try: return await client.test()
    finally: await client.close()


def main():
    parser = argparse.ArgumentParser(description='HCS IAM 认证诊断（输出结果不包含 Token / AK / SK）')
    parser.add_argument('-c','--config',default='config.yaml')
    args = parser.parse_args()
    try:
        result = asyncio.run(check(args.config))
        print(json.dumps(result,ensure_ascii=False,indent=2))
    except Exception as exc:
        # 配置或上游错误由连接层以安全消息反馈；不输出 repr、请求头和堆栈。
        from .hcs import HCSError
        message = str(exc) if isinstance(exc,(HCSError,ValueError)) else '认证诊断失败，请检查配置文件、主密钥和服务依赖'
        print(json.dumps({'ok':False,'message':message},ensure_ascii=False))
        raise SystemExit(1)


if __name__=='__main__': main()
