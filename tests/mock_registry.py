"""测试用的模拟 SWR（Docker Registry V2 + Bearer Token 鉴权）。

仓库状态保存在 JSON 文件中，fake_docker.py 推送镜像时会写入同一个文件。
"""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any, Dict

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

USER = "ndc302-region-1_his_beta@MOCKAK"
PASSWORD = "mock-login-key"
TOKEN = "mock-token"
MANIFEST_V2 = "application/vnd.docker.distribution.manifest.v2+json"


def load_state(path: Path) -> Dict[str, Any]:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"repos": {}}


def save_state(path: Path, state: Dict[str, Any]) -> None:
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def add_image(path: Path, repo: str, tag: str, layers: int = 5, arch: str = "arm64",
              created: str = "2024-01-01T00:00:00Z") -> None:
    state = load_state(path)
    state["repos"].setdefault(repo, {})[tag] = {"layers": layers, "arch": arch, "created": created}
    save_state(path, state)


def _config_blob(info: Dict[str, Any]) -> bytes:
    cfg = {"architecture": info["arch"], "os": "linux", "created": info["created"],
           "config": {"Labels": {"swr-builder.mode": info.get("mode", "full")}}}
    return json.dumps(cfg, sort_keys=True).encode()


def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def create_mock_registry(state_file: Path) -> FastAPI:
    app = FastAPI()
    expected_basic = "Basic " + base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()

    def authorized(request: Request) -> bool:
        return request.headers.get("authorization") == f"Bearer {TOKEN}"

    def challenge(scope: str = "") -> Response:
        header = 'Bearer realm="https://swr.mock/swr/auth/v2/registry/auth/",service="dockyard"'
        if scope:
            header += f',scope="{scope}"'
        return JSONResponse({"errors": [{"code": "UNAUTHORIZED"}]}, status_code=401,
                            headers={"WWW-Authenticate": header})

    @app.get("/swr/auth/v2/registry/auth/")
    def token(request: Request):
        if request.headers.get("authorization") != expected_basic:
            return JSONResponse({"errors": [{"code": "UNAUTHORIZED"}]}, status_code=401)
        return {"token": TOKEN, "expires_in": 3600}

    @app.get("/v2/")
    def ping(request: Request):
        return {} if authorized(request) else challenge()

    @app.get("/v2/{repo:path}/tags/list")
    def tags(repo: str, request: Request, n: int = 1000, last: str = ""):
        if not authorized(request):
            return challenge(f"repository:{repo}:pull")
        repos = load_state(state_file)["repos"]
        if repo not in repos:
            return JSONResponse({"errors": [{"code": "NAME_UNKNOWN"}]}, status_code=404)
        all_tags = sorted(repos[repo])
        if last:
            all_tags = [t for t in all_tags if t > last]
        page = all_tags[:n]
        headers = {}
        if len(all_tags) > n:
            headers["Link"] = f'</v2/{repo}/tags/list?n={n}&last={page[-1]}>; rel="next"'
        return JSONResponse({"name": repo, "tags": page}, headers=headers)

    @app.get("/v2/{repo:path}/manifests/{ref}")
    def manifest(repo: str, ref: str, request: Request):
        if not authorized(request):
            return challenge(f"repository:{repo}:pull")
        info = load_state(state_file)["repos"].get(repo, {}).get(ref)
        if not info:
            return JSONResponse({"errors": [{"code": "MANIFEST_UNKNOWN"}]}, status_code=404)
        blob = _config_blob(info)
        body = {
            "schemaVersion": 2, "mediaType": MANIFEST_V2,
            "config": {"mediaType": "application/vnd.docker.container.image.v1+json",
                       "size": len(blob), "digest": _digest(blob)},
            "layers": [{"mediaType": "application/vnd.docker.image.rootfs.diff.tar.gzip",
                        "size": 1024 * 1024, "digest": "sha256:" + f"{i:064x}"} for i in range(info["layers"])],
        }
        data = json.dumps(body).encode()
        return Response(data, media_type=MANIFEST_V2, headers={"Docker-Content-Digest": _digest(data)})

    @app.get("/v2/{repo:path}/blobs/{digest}")
    def blob(repo: str, digest: str, request: Request):
        if not authorized(request):
            return challenge(f"repository:{repo}:pull")
        return RedirectResponse(f"https://obs.mock/blobs/{repo}/{digest}", status_code=307)

    @app.get("/blobs/{repo:path}/{digest}")
    def blob_storage(repo: str, digest: str, request: Request):
        # 模拟对象存储：跨域重定向后不应该再带 Authorization
        assert "authorization" not in request.headers
        for info in load_state(state_file)["repos"].get(repo, {}).values():
            data = _config_blob(info)
            if _digest(data) == digest:
                return Response(data, media_type="application/octet-stream")
        return Response(status_code=404)

    return app
