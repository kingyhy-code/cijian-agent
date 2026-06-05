"""辞间 Agent 服务 —— 全部 AI 能力的唯一入口。

所有端点统一通过 AgentExecutor 执行，享受一致的：
- 会话消息持久化到 agent_messages
- 错误处理（不再吞异常返回假数据）
- 画像联动（评估类操作自动更新用户写作画像）

端点：
  /agent/chat         → LangGraph ReAct 循环（LLM 自主决策）
  /agent/chat/stream  → SSE 流式对话（边推理边返回）
  /agent/coach/*      → 创作教练（规则检测、评估、润色、帮写、对话）
  /agent/companion/*  → 阅读伴侣（文学分析、导读、对话、检验点评）
  /agent/progress/*   → 进度可视化（分数历史、练习统计、综合概览）
  /agent/knowledge/*  → 知识库（搜索、添加、统计）
"""

from __future__ import annotations

import json
import ssl

ssl._create_default_https_context = ssl._create_unverified_context

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from app.agent.executor import (
    agent_chat, agent_chat_stream,
    coach_analyze, coach_evaluate, coach_polish, coach_inspire, coach_chat_endpoint,
    review_document,
    comp_chat, comp_guide_chat,
    get_progress_scores, get_progress_exercise, get_progress_summary,
    knowledge_search, knowledge_add, knowledge_stats, knowledge_import_book,
)
from app.config import settings
from app.memory.store import init_db, get_messages, get_user_exercises, submit_exercise_answer, get_exercise_by_id
from app.models.schemas import AgentRequest, AgentResponse

logging.basicConfig(level=settings.log_level.upper(),
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("cijian-agent")

app = FastAPI(title="辞间 Agent 服务", version="3.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
STATIC_DIR = Path(__file__).parent.parent / "static"
STATIC_DIR.mkdir(exist_ok=True)


@app.on_event("startup")
def startup():
    init_db()
    # 自动初始化种子写作知识
    from app.services.vector_store import get_vector_store
    store = get_vector_store()
    if store.count() == 0:
        try:
            from data.seed_knowledge import init_seed_knowledge
            count = init_seed_knowledge()
            logger.info("知识库已初始化 %d 条种子写作知识", count)
        except Exception as e:
            logger.warning("种子知识初始化失败: %s", e)
    if store.count() < 30:
        try:
            from data.gaokao_knowledge import init_gaokao_knowledge
            count = init_gaokao_knowledge()
            logger.info("已导入 %d 条高考作文素材", count)
        except Exception as e:
            logger.warning("高考素材导入失败: %s", e)
    logger.info("Agent v3 启动，模型: %s", settings.ai_model)


# ═══════════════════════════════════════════════════════════════
# 通用
# ═══════════════════════════════════════════════════════════════

@app.get("/health")
async def health():
    return {"status": "ok", "service": "cijian-agent", "version": "3.0.0",
            "llm_available": bool(settings.ai_api_key)}


@app.get("/agent/health")
async def agent_health():
    return await health()


@app.get("/")
async def root():
    from fastapi.responses import FileResponse
    index = STATIC_DIR / "index.html"
    return FileResponse(index) if index.exists() else JSONResponse(
        {"service": "cijian-agent", "version": "3.0.0"})


# ═══════════════════════════════════════════════════════════════
# Agent 对话
# ═══════════════════════════════════════════════════════════════

@app.post("/agent/chat", response_model=AgentResponse)
async def agent_chat_endpoint(request: AgentRequest):
    result = await agent_chat(user_id=request.user_id, message=request.message,
                              session_id=request.session_id)
    return AgentResponse(
        reply=result["data"]["reply"],
        session_id=result["data"]["session_id"],
        tool_calls_made=result["data"].get("tool_calls_made", []),
    )


@app.post("/agent/chat/stream")
async def agent_chat_stream_endpoint(request: AgentRequest):
    """SSE 流式对话 — 实时返回思考过程、工具调用、令牌流"""
    async def event_generator():
        async for event in agent_chat_stream(
            user_id=request.user_id,
            message=request.message,
            session_id=request.session_id,
        ):
            yield f"event: {event['event']}\ndata: {json.dumps(event['data'], ensure_ascii=False)}\n\n"
    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/agent/history/{session_id}")
async def agent_history(session_id: str):
    return {"session_id": session_id, "messages": get_messages(session_id)}


# ═══════════════════════════════════════════════════════════════
# 创作教练 — 统一走 AgentExecutor
# ═══════════════════════════════════════════════════════════════

@app.post("/agent/coach/analyze")
async def coach_analyze_endpoint(request: dict):
    return coach_analyze(text=request.get("text", ""))

@app.post("/agent/coach/evaluate")
async def coach_evaluate_endpoint(request: dict):
    return coach_evaluate(text=request.get("text", ""))

@app.post("/agent/coach/polish")
async def coach_polish_endpoint(request: dict):
    return coach_polish(text=request.get("text", ""),
                        style=request.get("style", ""),
                        level=request.get("level", "medium"))

@app.post("/agent/coach/inspire")
async def coach_inspire_endpoint(request: dict):
    return coach_inspire(user_input=request.get("input", ""),
                         context=request.get("context", ""),
                         mode=request.get("mode", ""))

@app.post("/agent/coach/chat")
async def coach_chat_ep(request: dict):
    return coach_chat_endpoint(work_content=request.get("workContent", ""),
                               message=request.get("message", ""),
                               history=request.get("history", []))


# ═══════════════════════════════════════════════════════════════
# 阅读伴侣 — 仅保留前端 WorkDetail 仍在使用的端点
# ═══════════════════════════════════════════════════════════════

@app.post("/agent/companion/chat")
async def comp_chat_endpoint(request: dict):
    return comp_chat(text=request.get("text", ""),
                     question=request.get("question", ""),
                     messages=request.get("messages", []))

@app.post("/agent/companion/guide/chat")
async def comp_guide_chat_endpoint(request: dict):
    return comp_guide_chat(title=request.get("title", ""),
                           author=request.get("author", ""),
                           is_classic=request.get("isClassic", True),
                           work_content=request.get("workContent", ""),
                           message=request.get("message", ""),
                           history=request.get("history", []))



# ============================================================================
# 练习题管理
# ============================================================================

@app.get("/agent/exercises/{user_id}")
async def agent_exercises(user_id: str, status: str = ""):
    return {"code": 200, "message": "success", "data": get_user_exercises(user_id, status)}

@app.post("/agent/exercise/review")
async def agent_exercise_review(request: dict):
    exercise_id = request.get("exercise_id", "")
    user_answer = request.get("user_answer", "")
    user_id = request.get("user_id", "")
    if not exercise_id or not user_answer:
        return {"code": 400, "message": "缺少 exercise_id 或 user_answer"}
    ex_data = get_exercise_by_id(exercise_id)
    if not ex_data:
        return {"code": 404, "message": "练习题不存在"}
    from app.tools.exercise import review_exercise_answer, generate_exercise
    from app.models.schemas import Exercise
    ex = Exercise(
        type=ex_data["type"], topic=ex_data["topic"],
        question=ex_data["question"], options=ex_data.get("options"),
        answer=ex_data.get("answer", ""), explanation="",
        difficulty=ex_data.get("difficulty", "中等"),
    )
    try:
        review = review_exercise_answer(ex, user_answer)
        review_json = review.model_dump_json()
        submit_exercise_answer(exercise_id, user_answer, review_json)
        if user_id:
            try:
                from app.memory.store import record_exercise_result, update_skill_scores, get_evaluation_history
                record_exercise_result(user_id=user_id, topic=ex.topic, difficulty=ex.difficulty, is_correct=review.is_correct)
                # 更新画像分数（简单算法：正确+1分到相关维度）
                if review.is_correct and ex.topic:
                    update_skill_scores(user_id, {"词汇精准度": 1})
            except Exception:
                pass
        return {"code": 200, "message": "success", "data": review.model_dump()}
    except Exception as e:
        return {"code": 500, "message": str(e)}

# ═══════════════════════════════════════════════════════════════
# 任务查询
# ═══════════════════════════════════════════════════════════════

@app.post("/agent/plan/create")
async def agent_create_plan(request: dict):
    """直接创建学习计划，不经过 Agent 对话"""
    user_id = request.get("user_id", "")
    goal = request.get("goal", "")
    if not user_id:
        return {"code": 400, "message": "缺少 user_id"}
    from app.agent.tools import create_learning_plan as _clp
    _clp.invoke({"user_id": user_id, "goal": goal or "提升写作水平"})
    from app.memory.store import get_user_tasks, get_latest_plan_id
    plan_id = get_latest_plan_id(user_id)
    tasks = get_user_tasks(user_id)
    if plan_id:
        tasks = [t for t in tasks if t.get("plan_id") == plan_id]
    return {"code": 200, "message": "success", "data": {"tasks": tasks, "total": len(tasks)}}

@app.get("/agent/profile/{user_id}")
async def agent_profile_endpoint(user_id: str):
    """获取用户学习档案（字数、天数、徽章）"""
    from app.memory.store import get_profile, get_cumulative_stats
    profile = get_profile(user_id)
    stats = get_cumulative_stats(user_id)
    return {"code": 200, "message": "success", "data": {
        "user_id": user_id,
        "level": profile.level,
        "total_writings": profile.total_writings,
        "skill_scores": profile.skill_scores,
        "cumulative_words": stats["cumulative_words"],
        "learning_days": stats["learning_days"],
        "badges": stats["badges"],
    }}


@app.get("/agent/tasks/{user_id}")
async def agent_tasks_endpoint(user_id: str):
    """获取用户的最新学习计划任务（用于前端计划面板）"""
    from app.memory.store import get_user_tasks, get_latest_plan_id
    plan_id = get_latest_plan_id(user_id)
    if not plan_id:
        return {"code": 200, "message": "success", "data": {"tasks": [], "total": 0, "completed": 0, "percentage": 0, "plan_id": None}}
    tasks = get_user_tasks(user_id)
    # 只返回最新计划下的任务
    tasks = [t for t in tasks if t.get("plan_id") == plan_id]
    # 按 status 排序: in_progress > pending > completed
    status_order = {"in_progress": 0, "pending": 1, "completed": 2}
    tasks.sort(key=lambda t: (status_order.get(t.get("status", "pending"), 9), t.get("step_number", 0)))
    completed = sum(1 for t in tasks if t.get("status") == "completed")
    pct = round(completed / len(tasks) * 100, 1) if tasks else 0
    return {"code": 200, "message": "success", "data": {
        "tasks": tasks,
        "total": len(tasks),
        "completed": completed,
        "percentage": pct,
        "plan_id": plan_id,
        "all_done": pct == 100 and len(tasks) > 0,
    }}


# ═══════════════════════════════════════════════════════════════
# 进度可视化
# ═══════════════════════════════════════════════════════════════

@app.get("/agent/progress/scores/{user_id}")
async def progress_scores(user_id: str):
    """五维能力分数历史（用于折线图）"""
    return get_progress_scores(user_id)

@app.get("/agent/progress/exercise/{user_id}")
async def progress_exercise(user_id: str):
    """练习统计（正确率、难度分布）"""
    return get_progress_exercise(user_id)

@app.get("/agent/progress/summary/{user_id}")
async def progress_summary(user_id: str):
    """综合进度概览（里程碑、趋势）"""
    return get_progress_summary(user_id)



# ============================================================================
# 文档批改
# ============================================================================

@app.post("/agent/review")
async def agent_review_endpoint(request: dict):
    return review_document(
        title=request.get("title", "无标题"),
        content=request.get("content", ""),
        user_id=request.get("user_id", "default"),
        session_id=request.get("session_id", None),
    )

# ═══════════════════════════════════════════════════════════════
# 知识库管理
# ═══════════════════════════════════════════════════════════════

@app.post("/agent/knowledge/search")
async def knowledge_search_endpoint(request: dict):
    """语义搜索写作知识库"""
    return knowledge_search(query=request.get("query", ""),
                            k=request.get("k", 5))

@app.post("/agent/knowledge/add")
async def knowledge_add_endpoint(request: dict):
    """添加文档到知识库"""
    return knowledge_add(texts=request.get("texts", []),
                         metadatas=request.get("metadatas", None))

@app.get("/agent/knowledge/stats")
async def knowledge_stats_endpoint():
    """知识库统计信息"""
    return knowledge_stats()


@app.post("/agent/knowledge/import")
async def knowledge_import_endpoint(request: dict):
    """导入一本书到知识库（长文本自动分块）"""
    return knowledge_import_book(
        title=request.get("title", ""),
        content=request.get("content", ""),
        author=request.get("author", ""),
        category=request.get("category", "写作教学"),
    )
