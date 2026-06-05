"""LLM 后端抽象层 —— 隔离 DeepSeek（标准 tool calling）和千问（result backfill）的差异。

DeepSeek 原生支持 tool role，千问需要将工具结果回填为 HumanMessage。
通过 LLMBackend 抽象隔离差异，Agent 循环不感知后端类型。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import httpx
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage, BaseMessage
from langchain_openai import ChatOpenAI


@dataclass
class ToolResult:
    """工具执行结果"""
    tool_name: str
    tool_call_id: str = ""
    content: str = ""
    success: bool = True
    error: str | None = None
    truncated: bool = False  # content 是否被截断


class LLMBackend(ABC):
    """LLM 后端抽象基类"""

    @abstractmethod
    def create_llm(self, temperature: float, tools: list | None = None) -> BaseChatModel:
        """创建 LLM 实例。每次调用必须返回新实例，保证线程安全。"""

    @abstractmethod
    def wrap_tool_results(
        self, results: list[ToolResult], messages: list[BaseMessage]
    ) -> list[BaseMessage]:
        """将工具执行结果插入消息历史。
        DeepSeek: 为每个结果创建 ToolMessage（标准 tool role）
        千问: 将结果拼接为 HumanMessage（回填策略）
        """

    @staticmethod
    def extract_tool_calls(response) -> list[dict] | None:
        """从 LLM 响应中提取 tool_calls（平台无关）"""
        if hasattr(response, "tool_calls") and response.tool_calls:
            tcs = []
            for tc in response.tool_calls:
                if isinstance(tc, dict):
                    tcs.append(tc)
                else:
                    tcs.append({"name": tc.get("name", str(tc)),
                                "args": tc.get("args", {}),
                                "id": tc.get("id", "")})
            return tcs
        return None

    def strip_tool_calls(self, message: AIMessage) -> None:
        """移除消息上的 tool_calls 引用（某些后端不允许残留引用）"""

    @staticmethod
    def _format_result_text(results: list[ToolResult], max_chars: int = 4000) -> str:
        """将工具结果格式化为文本，超过 max_chars 截断并标记"""
        parts = []
        for r in results:
            content = r.content
            if len(content) > max_chars:
                content = content[:max_chars] + f"\n...(已截断 {len(r.content)} 字符)"
                r.truncated = True
            status = "" if r.success else f" [执行失败: {r.error}]"
            parts.append(f"[{r.tool_name} 结果]{status}:\n{content}")
        return "\n\n".join(parts)


# ═══════════════════════════════════════════════════════════════
# DeepSeek 后端 — 标准 OpenAI tool calling
# ═══════════════════════════════════════════════════════════════

class DeepSeekBackend(LLMBackend):
    """DeepSeek 原生支持 tool role，使用标准 ToolMessage"""

    def __init__(self, api_key: str, base_url: str, model: str):
        self._api_key = api_key
        self._base_url = base_url
        self._model = model
        self._http_client = httpx.Client(verify=False, timeout=180)

    def create_llm(self, temperature: float = 0.3, tools: list | None = None) -> BaseChatModel:
        llm = ChatOpenAI(
            model=self._model,
            api_key=self._api_key,
            base_url=self._base_url,
            temperature=temperature,
            request_timeout=180,
            http_client=self._http_client,
        )
        if tools:
            llm = llm.bind_tools(tools)
        return llm

    def wrap_tool_results(
        self, results: list[ToolResult], messages: list[BaseMessage]
    ) -> list[BaseMessage]:
        new_msgs = list(messages)
        for r in results:
            content = r.content
            if r.truncated:
                content += "\n[注意：以上结果已被截断，如需完整数据请缩小查询范围]"
            if not r.success:
                content = f"工具执行失败: {r.error}"
            new_msgs.append(ToolMessage(
                content=content,
                tool_call_id=r.tool_call_id,
                name=r.tool_name,
            ))
        return new_msgs

    def strip_tool_calls(self, message: AIMessage) -> None:
        """DeepSeek 不需要清理，保留 tool_calls 用于后续 ToolMessage 匹配"""
        pass


# ═══════════════════════════════════════════════════════════════
# 千问后端 — 结果回填为 HumanMessage
# ═══════════════════════════════════════════════════════════════

class QwenBackend(LLMBackend):
    """千问不兼容 tool role，工具结果回填为 HumanMessage"""

    def __init__(self, api_key: str, base_url: str, model: str):
        self._api_key = api_key
        self._base_url = base_url
        self._model = model
        self._http_client = httpx.Client(verify=False, timeout=180)

    def create_llm(self, temperature: float = 0.3, tools: list | None = None) -> BaseChatModel:
        llm = ChatOpenAI(
            model=self._model,
            api_key=self._api_key,
            base_url=self._base_url,
            temperature=temperature,
            request_timeout=180,
            http_client=self._http_client,
        )
        if tools:
            llm = llm.bind_tools(tools)
        return llm

    def wrap_tool_results(
        self, results: list[ToolResult], messages: list[BaseMessage]
    ) -> list[BaseMessage]:
        # 清空最后一条 AIMessage 的 tool_calls（千问不接受残留引用）
        last_msg = messages[-1]
        if isinstance(last_msg, AIMessage):
            last_msg.tool_calls = []
            last_msg.content = ""

        result_text = self._format_result_text(results)
        result_msg = HumanMessage(
            content=f"[工具执行结果]\n\n{result_text}\n\n"
                    f"请基于以上工具返回的数据，给用户一个综合分析报告。"
        )
        return list(messages) + [result_msg]

    def strip_tool_calls(self, message: AIMessage) -> None:
        """千问：必须清空 tool_calls 避免 API 格式报错"""
        message.tool_calls = []
        message.content = ""


# ═══════════════════════════════════════════════════════════════
# 工厂
# ═══════════════════════════════════════════════════════════════

def create_backend(api_key: str, base_url: str, model: str) -> LLMBackend:
    """根据 API 地址自动选择后端"""
    if "dashscope" in base_url.lower():
        return QwenBackend(api_key=api_key, base_url=base_url, model=model)
    return DeepSeekBackend(api_key=api_key, base_url=base_url, model=model)


__all__ = ["LLMBackend", "DeepSeekBackend", "QwenBackend", "ToolResult", "create_backend"]
