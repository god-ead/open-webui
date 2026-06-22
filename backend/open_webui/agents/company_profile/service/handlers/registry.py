""" Handler 注册中心 — 从环境变量加载并注册业务 Handler """

from __future__ import annotations

import importlib
import os
from typing import Any, Protocol


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
        """
        按路径加载并注册 Handler。

        Args:
            handler_path: 格式 ``"module.Class"`` 或 ``"package.module:ClassName"``
                          注意：路径分隔符是 ``.``，类名分隔符是 ``:``
        """
        module_path, class_name = handler_path.split(":", 1)
        module = importlib.import_module(module_path)
        handler_cls = getattr(module, class_name)
        handler = handler_cls.from_env()
        self.register(handler)

    def dispatch(
        self, task_type: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """
        根据 task_type 分发到对应 Handler 执行。

        Raises:
            ValueError: 如果 task_type 没有注册对应的 Handler
        """
        handler = self._handlers.get(task_type)
        if handler is None:
            raise ValueError(
                f"不支持的任务类型: {task_type}"
                f"（已注册: {list(self._handlers.keys())}）"
            )
        return handler.handle(payload)


def build_handler_registry() -> HandlerRegistry:
    """
    从环境变量 HANDLER_MODULES 构建 Handler 注册表。

    HANDLER_MODULES 格式::

        handlers.example:ExampleHandler,handlers.another:AnotherHandler

    每个条目格式为 ``module_path:ClassName``。
    """
    registry = HandlerRegistry()
    modules = os.getenv("HANDLER_MODULES", "")
    handler_paths = [
        path.strip() for path in modules.split(",") if path.strip()
    ]

    if not handler_paths:
        print(
            "[handler_registry] HANDLER_MODULES 未配置，未注册任何 handler — "
            "Worker 可以启动但无法处理任何任务"
        )
        return registry

    for handler_path in handler_paths:
        registry.add_handler_by_path(handler_path)
        print(f"[handler_registry] 已注册: {handler_path}")
    return registry
