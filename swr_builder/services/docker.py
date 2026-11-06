"""构建机 Docker 状态检测（带 30 秒缓存）。"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Callable, Dict, Optional

from ..config import Settings
from ..registry import normalize_arch

CACHE_SECONDS = 30


class DockerStatus:
    def __init__(self, settings: Settings, env: Callable[[], Dict[str, str]]):
        self.settings = settings
        self._env = env
        self._at = 0.0
        self._value: Optional[Dict[str, Any]] = None

    def invalidate(self) -> None:
        self._value = None

    async def get(self) -> Dict[str, Any]:
        now = time.monotonic()
        if self._value and now - self._at < CACHE_SECONDS:
            return self._value
        value: Dict[str, Any] = {"available": False, "version": "", "arch": "", "error": ""}
        docker_bin = self.settings.build.docker_bin
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                docker_bin, "version", "--format", "{{.Server.Version}} {{.Server.Arch}}",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=self._env())
            out, err = await asyncio.wait_for(proc.communicate(), 10)
            if proc.returncode == 0:
                parts = out.decode(errors="replace").split()
                value.update(available=True, version=parts[0] if parts else "",
                             arch=normalize_arch(parts[1]) if len(parts) > 1 else "")
            else:
                value["error"] = (err or out).decode(errors="replace").strip()[:300] or "docker 不可用"
        except FileNotFoundError:
            value["error"] = f"找不到 docker 命令：{docker_bin}"
        except asyncio.TimeoutError:
            value["error"] = "docker version 超时，请检查 Docker 服务"
            if proc and proc.returncode is None:
                proc.kill()
        self._at, self._value = now, value
        return value
