#!/usr/bin/env python3
"""测试用的假 docker 命令：记录调用参数，push 时把镜像写入模拟仓库的状态文件。

环境变量：
  FAKE_DOCKER_STATE   本地镜像状态文件（必填）
  MOCK_REGISTRY_STATE 模拟仓库状态文件（必填）
  FAKE_DOCKER_LOG     调用记录（JSON Lines）
  FAKE_DOCKER_ARCH    未指定 --platform 时构建结果的架构，默认 amd64（模拟 x86 构建机）
  FAKE_DOCKER_PASSWORD  docker login 期望的密码
"""
import json
import os
import re
import sys
import time
from pathlib import Path


def load(path):
    p = Path(path)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def save(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def split_ref(image):
    host, _, rest = image.partition("/")
    repo, _, tag = rest.rpartition(":")
    return host, repo, tag


def main(argv):
    if os.environ.get("FAKE_DOCKER_LOG"):
        with open(os.environ["FAKE_DOCKER_LOG"], "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"args": argv, "docker_config": os.environ.get("DOCKER_CONFIG")}) + "\n")
    local_file = os.environ["FAKE_DOCKER_STATE"]
    registry_file = os.environ["MOCK_REGISTRY_STATE"]
    cmd = argv[0] if argv else ""

    if cmd == "version":
        print("24.0.7 " + os.environ.get("FAKE_DOCKER_ARCH", "amd64"))
        return 0
    if cmd == "login":
        password = sys.stdin.read().strip()
        if os.environ.get("FAKE_DOCKER_PASSWORD") and password != os.environ["FAKE_DOCKER_PASSWORD"]:
            print("Error response from daemon: Get \"https://swr.mock/v2/\": unauthorized")
            return 1
        print("Login Succeeded")
        return 0
    if cmd == "build":
        args = argv[1:]
        dockerfile = args[args.index("-f") + 1]
        image = args[args.index("-t") + 1]
        platform = args[args.index("--platform") + 1] if "--platform" in args else ""
        context = Path(args[-1])
        text = Path(dockerfile).read_text(encoding="utf-8")
        base = re.search(r"^FROM\s+(\S+)", text, re.M).group(1)
        assert (context / "files").is_dir(), "构建上下文中缺少 files/ 目录"
        files = sorted(str(p.relative_to(context / "files")) for p in (context / "files").rglob("*") if p.is_file())
        print(f"Step 1/2 : FROM {base}")
        print(f"Step 2/2 : COPY files ({', '.join(files)})")
        print(f"Successfully tagged {image}")
        arch = platform.split("/")[1] if platform else os.environ.get("FAKE_DOCKER_ARCH", "amd64")
        _, base_repo, base_tag = split_ref(base)
        base_layers = load(registry_file).get("repos", {}).get(base_repo, {}).get(base_tag, {}).get("layers", 5)
        local = load(local_file)
        local[image] = {"arch": arch, "base": base, "layers": base_layers + 1, "files": files, "dockerfile": text}
        save(local_file, local)
        return 0
    if cmd == "image" and argv[1] == "inspect":
        info = load(local_file).get(argv[-1])
        if not info:
            print(f"Error: No such image: {argv[-1]}")
            return 1
        print(f"linux/{info['arch']}")
        return 0
    if cmd == "push":
        image = argv[1]
        info = load(local_file).get(image)
        if not info:
            print(f"An image does not exist locally with the tag: {image}")
            return 1
        _, repo, tag = split_ref(image)
        state = load(registry_file) or {"repos": {}}
        state.setdefault("repos", {}).setdefault(repo, {})[tag] = {
            "layers": info["layers"], "arch": info["arch"],
            "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "mode": "incremental"}
        save(registry_file, state)
        print(f"The push refers to repository [{image.rsplit(':', 1)[0]}]")
        print(f"{tag}: digest: sha256:{'ab' * 32} size: 1234")
        return 0
    if cmd in ("tag", "rmi"):
        print(f"{cmd} ok")
        return 0
    print(f"fake docker: unsupported command {argv}")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
