""" Handler 注册中心 — 从环境变量加载并注册业务 Handler """

from __future__ import annotations

import importlib
import logging
import os
from typing import Any, Protocol

logger = logging.getLogger("company_profile.handler_registry")


class TaskHandler(Protocol):
    """Handler 协议 — 所有业务 Handler 必须实现此接口"""

    #: 任务类型标识，如 ``"profile"``、``"analyze"``
    task_type: str

    def handle(self, payload: dict[str, Any]) -> dict[str, Any]:
        """
        处理任务的核心方法。

        Args:
            payload: 任务的 input 字段，业务自定义结构

        Returns:
            处理结果字典，会写入 tasks 表的 output 字段

        Raises:
            Exception: 任意异常都会被 main.py 捕获并标记任务为 failed
        """
        ...


class HandlerRegistry:
    """任务类型 → Handler 实例的注册表"""

    def __init__(self) -> None:
        self._handlers: dict[str, TaskHandler] = {}

    def register(self, handler: TaskHandler) -> None:
        """注册一个 Handler 实例"""
        if handler.task_type in self._handlers:
            raise ValueError(f"任务类型已注册: {handler.task_type}")
        self._handlers[handler.task_type] = handler

    def add_handler_by_path(self, handler_path: str) -> None:
        """按路径加载并注册 Handler，格式 ``module.path:ClassName``"""
        module_path, class_name = handler_path.split(":", 1)
        module = importlib.import_module(module_path)
        handler_cls = getattr(module, class_name)
        handler = handler_cls.from_env()
        self.register(handler)

    def dispatch(
        self, task_type: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """根据 task_type 分发到对应 Handler 执行"""
        handler = self._handlers.get(task_type)
        if handler is None:
            raise ValueError(
                f"不支持的任务类型: {task_type}"
                f"（已注册: {list(self._handlers.keys())}）"
            )
        return handler.handle(payload)


def build_handler_registry() -> HandlerRegistry:
    """从环境变量 HANDLER_MODULES 构建 Handler 注册表"""
    registry = HandlerRegistry()
    modules = os.getenv("HANDLER_MODULES", "")
    handler_paths = [
        path.strip() for path in modules.split(",") if path.strip()
    ]

    if not handler_paths:
        logger.warning(
            "HANDLER_MODULES 未配置，未注册任何 handler — "
            "Worker 可以启动但无法处理任何任务"
        )
        return registry

    for handler_path in handler_paths:
        registry.add_handler_by_path(handler_path)
        logger.info("已注册: %s", handler_path)
    return registry
