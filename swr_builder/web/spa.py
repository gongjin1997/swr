"""托管 Vue 前端构建产物（web/ 工程 `npm run build` 输出到 swr_builder/webui/）。

- /assets/*            带内容哈希的静态资源，公开，长期缓存（缓存头由中间件设置）
- /login               公开，返回 index.html
- 其他非 /api 页面路径  需要登录（中间件负责 303 到登录页），返回 index.html，由前端路由渲染
- 未构建前端时返回一个说明页，API 不受影响
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

WEBUI_DIR = Path(__file__).resolve().parent.parent / "webui"
NO_CACHE = {"Cache-Control": "no-cache"}

PLACEHOLDER = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>SWR 镜像构建平台</title></head>
<body style="font-family:sans-serif;max-width:640px;margin:80px auto;line-height:1.7">
<h2>前端页面尚未构建</h2>
<p>后端 API 正常运行。请在源码目录执行：</p>
<pre style="background:#f4f4f4;padding:12px">cd web &amp;&amp; npm ci &amp;&amp; npm run build</pre>
<p>构建产物会输出到 <code>swr_builder/webui/</code>。使用 Docker 镜像部署时会自动构建。</p>
</body></html>"""


def _index():
    index = WEBUI_DIR / "index.html"
    if index.is_file():
        return FileResponse(index, headers=NO_CACHE)
    return HTMLResponse(PLACEHOLDER, headers=NO_CACHE)


def install(app: FastAPI) -> None:
    """必须在所有 API 路由注册之后调用：兜底路由会匹配任意路径。"""
    @app.get("/assets/{path:path}", include_in_schema=False)
    async def asset(path: str):
        base = (WEBUI_DIR / "assets").resolve()
        target = (base / path).resolve()
        if base not in target.parents or not target.is_file():
            return JSONResponse({"detail": "文件不存在", "code": "NOT_FOUND"}, status_code=404)
        return FileResponse(target)

    @app.get("/login", include_in_schema=False)
    async def login_page():
        return _index()

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        if full_path == "api" or full_path.startswith("api/"):
            return JSONResponse({"detail": "接口不存在", "code": "NOT_FOUND"}, status_code=404)
        return _index()
