"""Agent 状态图 —— 真正的 ReAct 循环：LLM 自主推理 → 选择工具 → 观察结果 → 继续推理。

通过 LLMBackend 抽象层隔离 DeepSeek（标准 tool calling）和千问（result backfill）。
Agent 不强制调用顺序，LLM 根据用户意图自主决定调用哪些工具。
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, BaseMessage, ToolMessage
from langgraph.graph import StateGraph, END
from langgraph.graph.state import CompiledStateGraph
from typing_extensions import TypedDict

from app.agent.tools import ALL_TOOLS
from app.config import settings
from app.memory.store import save_message, get_messages
from app.services.llm_service import get_llm_adapter, is_available
from app.services.llm_adapter import ToolResult

logger = logging.getLogger("cijian-agent.graph")

SYSTEM_PROMPT = """你是「辞间教练」，一位专业的写作老师。全程用中文回复。禁止对用户说任何工具名称。

回复格式要求：可以使用 markdown 格式（如 **加粗**、### 标题、- 列表）让排版更清晰，但不要用表格和代码块。

规则：
1. 练习题：只在用户明确要求时用 create_exercise 生成练习。每次生成一道题，用 exercise_id 追踪。生成后在回复中告诉用户可去左侧"作业目录"查看和作答。不要主动出题。
2. 用户要求制定学习计划 → 直接生成结构化学习大纲，每步用"第X步：标题——内容"格式，不用元数据。
3. 用户发来文字 → 调 check_rules + deep_evaluate 分析后给出改进建议。
4. 闲聊/问候 → 简短回复，介绍自己但不建计划不出题。
5. 任务管理 → 开始教某一步时调 update_task_status 标记为 in_progress。完成教学后立即调 update_task_status 标记为 completed，并自动开始下一步。永远让学生的学习地图进度保持最新。"""



class AgentState(TypedDict):
    messages: list[BaseMessage]
    user_id: str
    session_id: str
    tool_calls: list[str]
    step_count: int
    plan_created: bool  # 计划已生成，禁止后续工具调用


def _check_token_budget(messages: list[BaseMessage], max_chars: int) -> bool:
    """检查消息历史是否超过字符预算（近似 token 估算）。返回 True 表示已超标。"""
    total = sum(len(str(m.content)) if hasattr(m, "content") and m.content else 0
                for m in messages)
    return total > max_chars


def _load_history(session_id: str, limit: int = 10) -> list[BaseMessage]:
    """从数据库加载最近 N 条对话消息，恢复会话上下文。"""
    try:
        rows = get_messages(session_id, limit=limit * 2)
    except Exception:
        return []
    result = []
    for m in rows:
        role = m.get("role", "")
        content = m.get("content", "")
        if not content:
            continue
        if role == "user":
            result.append(HumanMessage(content=content))
        elif role == "assistant":
            result.append(AIMessage(content=content))
    return result


def _get_teaching_hint(results: list[ToolResult]) -> str | None:
    """根据刚执行的工具，返回下一步教学引导。
    代码层状态机 —— 不依赖 LLM 自觉，强制执行教学闭环。"""
    names = {r.tool_name for r in results}

    if "create_exercise" in names:
        return ("【教学提示】你刚出了一道练习题。请向学生展示题目，等待他们提交答案。"
                "收到答案后必须用 review_answer 批改。")

    if "review_answer" in names:
        return ("【教学提示】批改完成。请判断结果：\n"
                "- 做对了 → 用 update_task_status 标记已完成 → 进入下一步\n"
                "- 做错了 → 分析错误原因，换角度再讲，用 create_exercise 出一道更简单的题\n"
                "不要只说对错就结束，必须有后续行动。")

    if "create_learning_plan" in names:
        return ("[系统指令 - 最高优先级] 学习计划已生成完毕。"
                "你的唯一任务：立即用文字展示计划给用户，然后结束本轮对话。"
                "绝对禁止：调用任何工具（包括 search_knowledge、create_exercise、review_answer 等）。"
                "绝对禁止：出题或开始教学。等待用户确认后再继续。")

    if "search_knowledge" in names:
        return ("【教学提示】你已检索到写作知识。如果学习计划已经创建好了，"
                "请直接展示计划，不要继续调用其他工具。如果还没创建计划，请立即用 create_learning_plan 创建。")

    if "get_writing_profile" in names:
        return ("【教学提示】你已了解学生水平。学生要求制定学习计划，"
                "现在必须立即调用 create_learning_plan 生成计划。"
                "不要问问题、不要讲解、不要出题。直接创建计划！")

    return None


def _agent_node(state: AgentState) -> dict[str, Any]:
    adapter = get_llm_adapter()
    llm = adapter.create_llm(tools=ALL_TOOLS)
    response = llm.invoke(state["messages"])

    tools = list(state.get("tool_calls", []))
    step = state.get("step_count", 0) + 1

    if hasattr(response, "tool_calls") and response.tool_calls:
        for tc in response.tool_calls:
            name = tc.get("name", str(tc))
            if name not in tools:
                tools.append(name)

    return {"messages": [response], "tool_calls": tools, "step_count": step}


def _tool_executor_node(state: AgentState) -> dict[str, Any]:
    last_msg = state["messages"][-1]
    if not hasattr(last_msg, "tool_calls") or not last_msg.tool_calls:
        return {"messages": [], "tool_calls": state.get("tool_calls", []),
                "step_count": state.get("step_count", 0)}

    adapter = get_llm_adapter()
    tool_map = {t.name: t for t in ALL_TOOLS}
    results: list[ToolResult] = []
    max_chars = settings.tool_result_max_chars

    for tc in last_msg.tool_calls:
        name = tc.get("name", str(tc))
        args = tc.get("args", {})
        tc_id = tc.get("id", "")
        func = tool_map.get(name)

        if func is None:
            results.append(ToolResult(tool_name=name, tool_call_id=tc_id,
                                      success=False, error=f"未知工具: {name}"))
            continue

        try:
            raw = str(func.invoke(args))
            truncated = len(raw) > max_chars
            if truncated:
                raw = raw[:max_chars]
            results.append(ToolResult(
                tool_name=name, tool_call_id=tc_id, content=raw,
                success=True, truncated=truncated,
            ))
            logger.info("Tool executed: %s (chars=%d, truncated=%s)", name, len(raw), truncated)
        except Exception as e:
            logger.exception("Tool %s failed", name)
            results.append(ToolResult(
                tool_name=name, tool_call_id=tc_id,
                success=False, error=str(e),
            ))

    # 使用适配器处理工具结果插入（DeepSeek: ToolMessage, 千问: HumanMessage 回填）
    updated_messages = adapter.wrap_tool_results(results, state["messages"])

    # 教学状态机：根据刚执行的工具，注入下一步教学引导（用 SystemMessage 提高优先级）
    hint = _get_teaching_hint(results)
    if hint:
        updated_messages.append(SystemMessage(content=hint))

    # 计算新增的消息（用于返回）
    new_msgs = updated_messages[len(state["messages"]):]

    plan_created = state.get("plan_created", False)
    if any(r.tool_name == "create_learning_plan" for r in results if r.success):
        plan_created = True
        logger.info("plan_created flag set to True")

    return {
        "messages": new_msgs,
        "tool_calls": state.get("tool_calls", []),
        "step_count": state.get("step_count", 0),
        "plan_created": plan_created,
    }


def _should_continue(state: AgentState) -> str:
    last_msg = state["messages"][-1]
    step = state.get("step_count", 0)
    tool_calls = state.get("tool_calls", [])

    # 安全边界：超过最大步数强制结束
    if step >= settings.agent_max_steps:
        logger.warning("Agent 达到最大步数限制 %d，强制结束", settings.agent_max_steps)
        return END

    # Token 预算检查：消息太长时强制结束
    if _check_token_budget(state["messages"], settings.agent_max_token_estimate):
        logger.warning("Agent 消息历史超过 token 预算 %d，强制结束", settings.agent_max_token_estimate)
        return END

    # 学习计划已生成 → LLM 想继续调工具时直接拦截
    if state.get("plan_created") and hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
        logger.info("拦截：plan_created=True，禁止继续调用工具，强制结束")
        return END

    if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
        return "tool_executor"
    return END


def build_graph() -> CompiledStateGraph:
    graph = StateGraph(AgentState)
    graph.add_node("agent", _agent_node)
    graph.add_node("tool_executor", _tool_executor_node)
    graph.set_entry_point("agent")

    graph.add_conditional_edges(
        "agent", _should_continue,
        {"tool_executor": "tool_executor", END: END},
    )
    graph.add_edge("tool_executor", "agent")

    return graph.compile()


def get_agent() -> CompiledStateGraph:
    """每次返回最新编译的图，动态绑定制裁限制"""
    return build_graph()


def _auto_advance_task(user_id: str, tool_calls: list[str]) -> None:
    try:
        from app.memory.store import get_user_tasks, update_task_status
        tasks = get_user_tasks(user_id)
        if not tasks:
            return
        # 检测本轮对话是否有教学行为
        teaching_signals = {"deep_evaluate", "check_rules", "review_answer", "create_exercise"}
        has_teaching = any(tc in teaching_signals for tc in tool_calls)
        if not has_teaching:
            return
        # 找到当前 in_progress 的任务，标记为 completed
        in_progress = [t for t in tasks if t.get("status") == "in_progress"]
        if in_progress:
            current = in_progress[0]
            update_task_status(current["task_id"], "completed")
            # 找到下一个 pending 任务，标记为 in_progress
            pending = [t for t in tasks if t.get("status") == "pending"]
            if pending:
                next_task = min(pending, key=lambda t: t.get("step_number", 0))
                update_task_status(next_task["task_id"], "in_progress")
    except Exception:
        pass

async def run_agent(user_id: str, message: str,
                    session_id: str | None = None) -> dict[str, Any]:
    """执行一次 Agent 对话。

    返回: {"reply": str, "session_id": str, "tool_calls": list[str]}
    """
    if not is_available():
        return {"reply": "AI 服务未配置，请设置 AI_API_KEY 环境变量。",
                "session_id": session_id or "none", "tool_calls": []}

    sid = session_id or f"sess-{id(message) % 100000:05d}"
    save_message(sid, user_id, "user", message)

    # 加载会话历史，Agent 能记住之前讲到哪了
    history_msgs = _load_history(sid) if session_id else []

    state: AgentState = {
        "messages": [
            SystemMessage(content=SYSTEM_PROMPT),
            SystemMessage(content=f"当前学生 ID: {user_id}。调用任何需要 user_id 参数的工具时，必须使用 {user_id}。"),
            *history_msgs,
            HumanMessage(content=message),
        ],
        "user_id": user_id,
        "session_id": sid,
        "tool_calls": [],
        "step_count": 0,
        "plan_created": False,
    }

    graph = get_agent()
    result = await graph.ainvoke(
        state,
        config={"recursion_limit": settings.agent_max_steps},
    )

    # 从最终消息中提取回复
    reply = ""
    all_tools: list[str] = []
    for msg in result.get("messages", []):
        if isinstance(msg, AIMessage) and msg.content and not msg.tool_calls:
            reply = str(msg.content)
    all_tools = result.get("tool_calls", [])

    if not reply:
        reply = "我已完成分析。有什么想深入探讨的吗？"

    save_message(sid, user_id, "assistant", reply)

    # 自动推进学习任务：如果本次对话有教学行为，尝试标记当前任务为完成
    _auto_advance_task(user_id, all_tools)

    return {"reply": reply, "session_id": sid, "tool_calls": all_tools}


async def run_agent_stream(user_id: str, message: str,
                           session_id: str | None = None):
    """流式执行 Agent 对话，逐事件 yield SSE 数据。

    Yields: {"event": str, "data": dict}
    """
    if not is_available():
        yield {"event": "error", "data": {"message": "AI 服务未配置，请设置 AI_API_KEY 环境变量。"}}
        yield {"event": "done", "data": {"reply": "AI 服务未配置", "session_id": session_id or "none", "tool_calls": []}}
        return

    sid = session_id or f"sess-{id(message) % 100000:05d}"
    save_message(sid, user_id, "user", message)

    # 加载会话历史
    history_msgs = _load_history(sid) if session_id else []

    state: AgentState = {
        "messages": [
            SystemMessage(content=SYSTEM_PROMPT),
            SystemMessage(content=f"当前学生 ID: {user_id}。调用任何需要 user_id 参数的工具时，必须使用 {user_id}。"),
            *history_msgs,
            HumanMessage(content=message),
        ],
        "user_id": user_id,
        "session_id": sid,
        "tool_calls": [],
        "step_count": 0,
        "plan_created": False,
    }

    graph = get_agent()
    all_tools: list[str] = []
    full_reply_parts: list[str] = []

    try:
        async for event in graph.astream(state, config={"recursion_limit": settings.agent_max_steps}):
            # 节点完成事件
            for node_name, node_output in event.items():
                if node_name == "agent":
                    messages = node_output.get("messages", [])
                    for msg in messages:
                        if isinstance(msg, AIMessage):
                            if msg.tool_calls:
                                # 工具调用
                                for tc in msg.tool_calls:
                                    name = tc.get("name", str(tc))
                                    if name not in all_tools:
                                        all_tools.append(name)
                                    yield {
                                        "event": "tool_call",
                                        "data": {
                                            "name": name,
                                            "args": tc.get("args", {}),
                                        },
                                    }
                            elif msg.content:
                                # 文本回复
                                full_reply_parts.append(str(msg.content))
                                yield {
                                    "event": "reply",
                                    "data": {"content": str(msg.content)},
                                }
                elif node_name == "tool_executor":
                    yield {
                        "event": "tools_done",
                        "data": {"tool_count": len(all_tools)},
                    }
    except Exception as e:
        logger.exception("Agent 流式执行异常")
        yield {"event": "error", "data": {"message": str(e)}}

    full_reply = "".join(full_reply_parts)
    if not full_reply:
        full_reply = "我已完成分析。有什么想深入探讨的吗？"

    save_message(sid, user_id, "assistant", full_reply)
    yield {
        "event": "done",
        "data": {
            "reply": full_reply,
            "session_id": sid,
            "tool_calls": all_tools,
        },
    }
