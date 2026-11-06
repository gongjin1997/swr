"""进程内事件总线：构建、部署等模块之间解耦。

已发布事件：
- build.succeeded / build.failed   载荷：job（构建记录）
- deploy.succeeded / deploy.failed 载荷：deployment（部署记录）

后续的通知（企业微信/钉钉/邮件）、自动部署、制品扫描等能力只需订阅事件即可接入。
"""
from __future__ import annotations

import asyncio
import inspect
import logging
from collections import defaultdict
from typing import Any, Awaitable, Callable, Dict, List, Union

logger = logging.getLogger("swr_builder")
Handler = Callable[[Dict[str, Any]], Union[None, Awaitable[None]]]


class EventBus:
    def __init__(self) -> None:
        self._handlers: Dict[str, List[Handler]] = defaultdict(list)
        self._tasks: set = set()

    def subscribe(self, event: str, handler: Handler) -> None:
        self._handlers[event].append(handler)

    def emit(self, event: str, payload: Dict[str, Any]) -> None:
        for handler in list(self._handlers.get(event, [])) + list(self._handlers.get("*", [])):
            try:
                result = handler(dict(payload, event=event))
                if inspect.isawaitable(result):
                    task = asyncio.ensure_future(result)
                    self._tasks.add(task)
                    task.add_done_callback(self._done)
            except Exception:  # noqa: BLE001 订阅方的错误不能影响主流程
                logger.exception("事件 %s 的处理函数出错", event)

    def _done(self, task: "asyncio.Future[Any]") -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception():
            logger.error("事件处理失败：%s", task.exception())

    async def drain(self) -> None:
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
