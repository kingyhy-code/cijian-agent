"""统一工具执行框架 —— 所有端点共用的生命周期管理。

Agent 循环（/agent/chat）和直接端点（/agent/coach/*, /agent/companion/*）
都通过此框架执行，确保：
- 会话消息统一持久化到 agent_messages 表
- 错误处理一致性（不再吞异常返回假数据）
- 画像联动（评估类操作自动更新用户画像）
- 响应格式统一
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from app.agent.graph import run_agent
from app.memory.store import save_message, save_evaluation, update_skill_scores
from app.models.schemas import (
    AgentResponse, EvalDimension, Exercise, ExerciseReview,
)
from app.services.llm_service import is_available
from app.tools.prompts import (
    evaluate_text, polish_text, inspire_writing, coach_chat,
    companion_chat, companion_guide_chat,
)
from app.tools.rules import check_l1_l3

logger = logging.getLogger("cijian-agent.executor")

_pending_exercises: dict[str, Exercise] = {}


class ExecutorError(Exception):
    """工具执行错误"""
    def __init__(self, message: str, code: int = 500, data: dict | None = None):
        super().__init__(message)
        self.code = code
        self.data = data


# ═══════════════════════════════════════════════════════════════
# 统一响应内辅助
# ═══════════════════════════════════════════════════════════════

def _ok(data: Any, message: str = "success") -> dict:
    return {"code": 200, "message": message, "data": data, "error": None}


def _err(message: str, code: int = 500, data: Any = None, error: str = "internal_error") -> dict:
    return {"code": code, "message": message, "data": data, "error": error}


def _ensure_session(session_id: str | None = None, user_id: str = "default") -> str:
    """确保有合法的 session_id"""
    return session_id or f"direct-{user_id}-{uuid.uuid4().hex[:8]}"


# ═══════════════════════════════════════════════════════════════
# Agent 对话
# ═══════════════════════════════════════════════════════════════

async def agent_chat(user_id: str, message: str, session_id: str | None = None) -> dict:
    """执行 Agent ReAct 循环对话。返回 AgentResponse 格式。"""
    try:
        result = await run_agent(user_id=user_id, message=message, session_id=session_id)
        return _ok({
            "reply": result["reply"],
            "session_id": result["session_id"],
            "tool_calls_made": result.get("tool_calls", []),
        })
    except Exception:
        logger.exception("Agent 对话失败")
        return _err("处理请求时出现内部错误，请重试", code=500, data={
            "reply": "抱歉，处理请求时出现内部错误，请重试。",
            "session_id": session_id or "",
            "tool_calls_made": [],
        })


# ═══════════════════════════════════════════════════════════════
# 创作教练
# ═══════════════════════════════════════════════════════════════

def coach_analyze(text: str, user_id: str = "default", session_id: str | None = None) -> dict:
    """L1-L3 规则检测"""
    try:
        sid = _ensure_session(session_id, user_id)
        save_message(sid, user_id, "user", f"[/agent/coach/analyze] 检测文本")
        result = check_l1_l3(text)
        save_message(sid, user_id, "assistant",
                     f"检测完成，发现问题 {result['total']} 处")
        return _ok(result)
    except Exception as e:
        logger.exception("规则检测失败")
        return _err("规则检测失败", code=502, data={"total": 0, "summary": {}, "suggestions": []})


def coach_evaluate(text: str, user_id: str = "default", session_id: str | None = None) -> dict:
    """五维度深度评估，自动持久化评估记录和更新画像"""
    try:
        if not is_available():
            return _err("AI 服务未配置，请设置 AI_API_KEY", code=503,
                        data={"dimensions": [], "highlight": "", "improvement": "", "overall_score": 0})

        sid = _ensure_session(session_id, user_id)
        save_message(sid, user_id, "user", f"[评估请求] 文本长度 {len(text)} 字")

        data = evaluate_text(text)
        dims = data.get("dimensions", [])
        overall = data.get("overall_score", 0)

        # 持久化评估记录和画像更新
        if dims:
            try:
                eval_dims = [EvalDimension(
                    name=d.get("name", ""), score=d.get("score", 0),
                    comment=d.get("comment", ""), suggestion=d.get("suggestion", ""),
                ) for d in dims if isinstance(d, dict)]
                save_evaluation(user_id, text, eval_dims)
                update_skill_scores(user_id, eval_dims)
                logger.info("评估已保存: user=%s, overall=%d", user_id, overall)
            except Exception:
                logger.exception("保存评估记录失败")

        summary = f"评估完成，综合得分 {overall} 分"
        save_message(sid, user_id, "assistant", summary)
        return _ok(data)
    except Exception as e:
        logger.exception("深度评估失败")
        return _err("深度评估失败", code=502,
                    data={"dimensions": [], "highlight": "", "improvement": "", "overall_score": 0})


def coach_polish(text: str, style: str = "", level: str = "medium",
                 user_id: str = "default", session_id: str | None = None) -> dict:
    """分级润色"""
    try:
        if not is_available():
            return _err("AI 服务未配置", code=503, data={"polished": "", "changes": ""})
        sid = _ensure_session(session_id, user_id)
        save_message(sid, user_id, "user", f"[润色请求] level={level}")
        data = polish_text(text, style, level)
        save_message(sid, user_id, "assistant", f"润色完成（{level} 级别）")
        return _ok(data)
    except Exception as e:
        logger.exception("润色失败")
        return _err("润色失败", code=502, data={"polished": "", "changes": ""})


def coach_inspire(user_input: str, context: str = "", mode: str = "",
                  user_id: str = "default", session_id: str | None = None) -> dict:
    """帮写"""
    try:
        if not is_available():
            return _err("AI 服务未配置", code=503, data={"mode": "inspire", "results": []})
        sid = _ensure_session(session_id, user_id)
        save_message(sid, user_id, "user", f"[帮写请求] mode={mode or 'auto'}")
        data = inspire_writing(user_input, context, mode)
        save_message(sid, user_id, "assistant", f"帮写完成（{data.get('mode', 'inspire')}）")
        return _ok(data)
    except Exception as e:
        logger.exception("帮写失败")
        return _err("帮写失败", code=502, data={"mode": "inspire", "results": []})


def coach_chat_endpoint(work_content: str, message: str, history: list | None = None,
                        user_id: str = "default", session_id: str | None = None) -> dict:
    """教练自由对话"""
    try:
        if not is_available():
            return _err("AI 服务未配置", code=503, data={"reply": ""})
        sid = _ensure_session(session_id, user_id)
        save_message(sid, user_id, "user", message)
        hist_text = _fmt_history(history or [])
        reply = coach_chat(work_content, message, hist_text)
        save_message(sid, user_id, "assistant", reply or "")
        return _ok({"reply": reply or "请稍后再试"})
    except Exception as e:
        logger.exception("教练对话失败")
        return _err("教练对话失败", code=502, data={"reply": ""})


# ═══════════════════════════════════════════════════════════════
# 阅读伴侣
# ═══════════════════════════════════════════════════════════════

def comp_chat(text: str, question: str, messages: list | None = None,
              user_id: str = "default", session_id: str | None = None) -> dict:
    """选段文学讨论"""
    try:
        if not is_available():
            return _err("AI 服务未配置", code=503, data={"reply": ""})
        sid = _ensure_session(session_id, user_id)
        save_message(sid, user_id, "user", question)
        hist_text = _fmt_history(messages or [])
        reply = companion_chat(text, question, hist_text)
        save_message(sid, user_id, "assistant", reply or "")
        return _ok({"reply": reply or "请稍后再试"})
    except Exception as e:
        logger.exception("文学讨论失败")
        return _err("文学讨论失败", code=502, data={"reply": ""})


def comp_guide_chat(title: str, author: str, is_classic: bool, work_content: str,
                    message: str, history: list | None = None,
                    user_id: str = "default", session_id: str | None = None) -> dict:
    """对话式导读"""
    try:
        if not is_available():
            return _err("AI 服务未配置", code=503, data={"reply": ""})
        sid = _ensure_session(session_id, user_id)
        save_message(sid, user_id, "user", message or "[导读请求]")
        hist_text = _fmt_history(history or [])
        reply = companion_guide_chat(title, author, is_classic, work_content, message, hist_text)
        save_message(sid, user_id, "assistant", reply or "")
        return _ok({"reply": reply or "请稍后再试"})
    except Exception as e:
        logger.exception("导读对话失败")
        return _err("导读对话失败", code=502, data={"reply": ""})


# ═══════════════════════════════════════════════════════════════
# 辅助
# ═══════════════════════════════════════════════════════════════

def _fmt_history(msgs: list) -> str:
    if not msgs:
        return ""
    return "\n".join(
        f"{'作者' if m.get('role') == 'user' else '教练'}：{m.get('content', '')}"
        for m in msgs
    )


# ═══════════════════════════════════════════════════════════════
# 进度可视化
# ═══════════════════════════════════════════════════════════════

def get_progress_scores(user_id: str) -> dict:
    """五维能力分数历史（用于折线图）"""
    try:
        from app.memory.store import get_skill_score_history, get_profile
        history = get_skill_score_history(user_id)
        profile = get_profile(user_id)
        return _ok({
            "user_id": user_id,
            "current_scores": profile.skill_scores,
            "history": history,
        })
    except Exception as e:
        logger.exception("获取分数历史失败")
        return _err("获取分数历史失败", code=502, data={"user_id": user_id, "current_scores": {}, "history": []})


def get_progress_exercise(user_id: str) -> dict:
    """练习统计（准确率、难度分布）"""
    try:
        from app.memory.store import get_user_accuracy, get_exercise_results
        accuracy = get_user_accuracy(user_id)
        results = get_exercise_results(user_id, limit=50)
        return _ok({
            "user_id": user_id,
            "total_exercises": accuracy.get("total_exercises", 0),
            "correct_count": accuracy.get("correct_count", 0),
            "overall_accuracy": accuracy.get("overall_accuracy", 0),
            "recent_5_accuracy": accuracy.get("recent_5_accuracy", 0),
            "by_difficulty": accuracy.get("by_difficulty", {}),
            "recent_results": results[:20],
        })
    except Exception as e:
        logger.exception("获取练习统计失败")
        return _err("获取练习统计失败", code=502, data={"user_id": user_id, "total_exercises": 0, "overall_accuracy": 0})


def get_progress_summary(user_id: str) -> dict:
    """综合进度概览（里程碑、趋势、任务完成度）"""
    try:
        from app.memory.store import get_profile, get_user_accuracy, get_user_tasks
        profile = get_profile(user_id)
        accuracy = get_user_accuracy(user_id)
        tasks = get_user_tasks(user_id)

        # 计算技能趋势（与初始值对比）
        skill_trend = []
        for dim, current in profile.skill_scores.items():
            skill_trend.append({
                "dimension": dim,
                "current": current,
                "trend": "up" if current > 50 else ("down" if current < 30 else "stable"),
            })

        # 里程碑
        milestones: list[dict] = []
        total_writings = profile.total_writings or 0
        total_ex = accuracy.get("total_exercises", 0)
        if total_writings >= 1:
            milestones.append({"milestone": "first_evaluation", "achieved": True, "label": "完成首次评估"})
        if total_writings >= 5:
            milestones.append({"milestone": "five_evaluations", "achieved": True, "label": "累计 5 次评估"})
        if total_ex >= 1:
            milestones.append({"milestone": "first_exercise", "achieved": True, "label": "完成首次练习"})
        if total_ex >= 10:
            milestones.append({"milestone": "ten_exercises", "achieved": True, "label": "累计 10 次练习"})
        if accuracy.get("overall_accuracy", 0) >= 80:
            milestones.append({"milestone": "high_accuracy", "achieved": True, "label": "正确率达到 80%+"})
        if total_writings >= 10:
            milestones.append({"milestone": "ten_evaluations", "achieved": True, "label": "累计 10 次评估"})
        milestones.append({"milestone": "ten_evaluations", "achieved": False, "label": "累计 10 次评估 (进行中)" if total_writings > 0 else "开始第一次评估"})

        completed_tasks = sum(1 for t in tasks if t.get("status") == "completed")

        return _ok({
            "user_id": user_id,
            "level": profile.level,
            "total_writings": total_writings,
            "total_exercises": total_ex,
            "overall_accuracy": accuracy.get("overall_accuracy", 0),
            "skill_scores": profile.skill_scores,
            "skill_trend": skill_trend,
            "completed_tasks": completed_tasks,
            "total_tasks": len(tasks),
            "milestones": milestones,
            "recent_activity": [],
        })
    except Exception as e:
        logger.exception("获取进度概要失败")
        return _err("获取进度概要失败", code=502, data={"user_id": user_id})


# ═══════════════════════════════════════════════════════════════
# 知识库管理（RAG）
# ═══════════════════════════════════════════════════════════════

def knowledge_search(query: str, k: int = 5) -> dict:
    """语义搜索写作知识库"""
    try:
        from app.services.vector_store import get_vector_store
        store = get_vector_store()
        if store.count() == 0:
            return _ok({"query": query, "count": 0, "results": [], "hint": "知识库为空，可通过 /agent/knowledge/add 添加内容"})
        docs, metas, distances = store.search(query, k)
        results = [{
            "relevance": round(d, 4) if isinstance(d, float) else 0,
            "content": doc[:800],
            "source": meta.get("source", "") if meta else "",
        } for doc, meta, d in zip(docs, metas, distances)]
        return _ok({"query": query, "count": len(results), "results": results})
    except Exception as e:
        logger.exception("知识库搜索失败")
        return _err(f"知识库搜索失败: {e}", code=502, data={"query": query, "count": 0, "results": []})


def knowledge_add(texts: list[str], metadatas: list[dict] | None = None) -> dict:
    """向知识库添加文档"""
    try:
        from app.services.vector_store import get_vector_store
        store = get_vector_store()
        ids = store.add_texts(texts, metadatas)
        return _ok({"added": len(ids), "ids": ids})
    except Exception as e:
        logger.exception("知识库添加失败")
        return _err(f"知识库添加失败: {e}", code=502, data={"added": 0, "ids": []})


def knowledge_stats() -> dict:
    """知识库统计信息"""
    try:
        from app.services.vector_store import get_vector_store
        store = get_vector_store()
        return _ok({"total_documents": store.count(), "status": "active"})
    except Exception as e:
        logger.exception("知识库状态查询失败")
        return _err(f"知识库状态查询失败: {e}", code=502, data={"total_documents": 0, "status": "error"})


def knowledge_import_book(title: str, content: str, author: str = "",
                          category: str = "写作教学") -> dict:
    """导入一本书到知识库。长文本自动分块。"""
    try:
        from app.services.vector_store import get_vector_store
        store = get_vector_store()
        meta = {"title": title, "author": author, "category": category, "source": "book"}
        count = store.add_documents_chunked([content], metadatas=[meta])
        return _ok({"title": title, "chunks": count, "total_docs": store.count()})
    except Exception as e:
        logger.exception("导入书籍失败")
        return _err(f"导入书籍失败: {e}", code=502, data={"title": title, "chunks": 0})


# ═══════════════════════════════════════════════════════════════
# SSE 流式对话
# ═══════════════════════════════════════════════════════════════

async def agent_chat_stream(user_id: str, message: str,
                            session_id: str | None = None):
    """SSE 流式 Agent 对话 — 异步生成器，逐事件 yield"""
    from app.agent.graph import run_agent_stream
    async for event in run_agent_stream(user_id, message, session_id):
        yield event


# ============================================================================
# 文档批改
# ============================================================================

def review_document(title: str, content: str, user_id: str = "default", session_id: str | None = None) -> dict:
    try:
        from app.prompts.registry import PromptRegistry
        from app.services.llm_service import get_llm
        from langchain_core.messages import HumanMessage
        sid = _ensure_session(session_id, user_id)
        save_message(sid, user_id, "user", f"[/agent/review] 提交文档批改: {title}")
        prompt = PromptRegistry.get("review_document", title=title or "无标题", content=content)
        llm = get_llm(temperature=0.7)
        msg = HumanMessage(content=prompt)
        resp = llm.invoke([msg])
        reply = resp.content.strip() if hasattr(resp, 'content') else str(resp)
        save_message(sid, user_id, "assistant", reply[:2000])
        return _ok({
            "review": reply,
            "title": title,
            "session_id": sid,
        })
    except Exception:
        logger.exception("文档批改失败")
        return _err("文档批改失败，请重试", code=502, data={"review": "抱歉，批改服务暂时不可用。", "title": title})
