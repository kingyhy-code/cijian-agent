"""LLM 服务 —— 线程安全的 LLM 实例创建 + 后端自动选择。

每次调用 get_llm() 都创建新 ChatOpenAI 实例，消除单例温度竞态。
通过 get_llm_adapter() 获取当前后端（DeepSeek / 千问）。
"""

from __future__ import annotations

import httpx
from langchain_openai import ChatOpenAI

from app.config import settings
from app.services.llm_adapter import LLMBackend, create_backend

_http_client = httpx.Client(verify=False, timeout=180)
_backend: LLMBackend | None = None


def get_llm(temperature: float = 0.3) -> ChatOpenAI:
    """创建新 LLM 实例（线程安全，每次调用返回独立实例）"""
    return ChatOpenAI(
        model=settings.ai_model,
        api_key=settings.ai_api_key,
        base_url=settings.ai_base_url,
        temperature=temperature,
        request_timeout=180,
        http_client=_http_client,
    )


def get_llm_with_tools(tools: list) -> ChatOpenAI:
    """创建带工具绑定的 LLM 实例"""
    llm = get_llm()
    return llm.bind_tools(tools)


def get_llm_adapter() -> LLMBackend:
    """获取当前 LLM 后端适配器（单例，只读无竞争）"""
    global _backend
    if _backend is None:
        _backend = create_backend(
            api_key=settings.ai_api_key,
            base_url=settings.ai_base_url,
            model=settings.ai_model,
        )
    return _backend


def is_available() -> bool:
    return bool(settings.ai_api_key)


# 重新导出
__all__ = ["get_llm", "get_llm_with_tools", "get_llm_adapter", "is_available"]
