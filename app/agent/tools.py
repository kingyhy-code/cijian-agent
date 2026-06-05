"""Agent 工具注册 —— LLM 自主决定何时调用哪个工具。所有 AI 能力集中在 Agent。

工具描述采用信息式风格：说明工具做什么、返回什么、特点和限制，
让 LLM 根据用户意图自主推理何时调用，而非被动执行预设流程。
"""

import json
import uuid
from typing import Any

from langchain_core.tools import tool

from app.tools.rules import check_l1_l3
from app.tools.prompts import evaluate_text
from app.tools.exercise import generate_exercise as gen_ex, review_exercise_answer as review_ex
from app.memory.store import (
    get_profile, save_profile, update_skill_scores,
    save_evaluation, get_evaluation_history,
    save_exercise, get_exercise_history,
    record_exercise_result, get_user_accuracy,
    save_learning_plan,
    save_task, get_user_tasks, update_task_status as store_update_task, get_plan_progress,
    save_work, get_user_works, get_work_detail,
)
from app.models.schemas import WritingProfile, Exercise, LearningTask
from app.services.llm_service import is_available

_pending_exercises: dict[str, Exercise] = {}

# 难度等级映射
DIFFICULTY_ORDER = ["简单", "中等", "困难"]


def _resolve_auto_difficulty(user_id: str, topic: str) -> str:
    """根据用户历史正确率自动决定难度。"""
    if not user_id:
        return "中等"
    accuracy = get_user_accuracy(user_id, topic=topic if topic else None)
    recent = accuracy.get("recent_5_accuracy", 0)
    if recent == 0:
        # 无历史记录，从"中等"开始
        return "中等"
    # 根据最近 5 次正确率调整
    if recent > 80:
        # 升一档
        idx = DIFFICULTY_ORDER.index("中等") + 1
    elif recent < 50:
        # 降一档
        idx = DIFFICULTY_ORDER.index("中等") - 1
    else:
        idx = DIFFICULTY_ORDER.index("中等")
    return DIFFICULTY_ORDER[max(0, min(idx, len(DIFFICULTY_ORDER) - 1))]


# ═══════════════════════════════════════════════════════════════
# 原有工具
# ═══════════════════════════════════════════════════════════════

@tool
def check_rules(text: str) -> str:
    """检查文本中的错别字、的得地误用、长句（>80字）、被动语态、网络用语、冗余虚词。
    纯正则匹配，毫秒级完成，不消耗 token。返回按 L1/L2/L3 分类的问题列表及位置信息。
    适合作为初步快速筛查，但无法检测语义层面的问题（如逻辑矛盾、词汇不当）。"""
    result = check_l1_l3(text)
    if result["total"] == 0:
        return "未发现基础问题。"
    return json.dumps(result, ensure_ascii=False, indent=2)


@tool
def deep_evaluate(text: str, user_id: str = "") -> str:
    """从逻辑连贯性、词汇精准度、情感感染力、结构节奏、表达简洁性五个维度评估写作质量。
    每维度 0-100 分，含亮点评析和改善建议。需要 LLM 调用，耗时约 3-10 秒。
    评估结果会自动存入用户历史记录中供后续追踪。"""
    data = evaluate_text(text)
    dims = data.get("dimensions", [])
    if user_id and dims:
        try:
            from app.models.schemas import EvalDimension
            eval_dims = [EvalDimension(
                name=d.get("name", ""), score=d.get("score", 0),
                comment=d.get("comment", ""), suggestion=d.get("suggestion", ""),
            ) for d in dims if isinstance(d, dict)]
            save_evaluation(user_id, text, eval_dims)
            update_skill_scores(user_id, eval_dims)
            # 自动保存文章全文到作品库
            try:
                save_work(user_id, text)
            except Exception:
                pass
        except Exception:
            pass
    return json.dumps(data, ensure_ascii=False, indent=2)


@tool
def get_writing_profile(user_id: str) -> str:
    """读取用户的写作画像：强弱项列表、风格标签、五维度技能分数（滑动平均）、
    历史写作数量。用于了解用户背景后给出个性化建议。从 SQLite 读取，毫秒级。"""
    profile = get_profile(user_id)
    return json.dumps(profile.model_dump(), ensure_ascii=False, indent=2)


@tool
def update_profile(user_id: str, strengths: list[str] | None = None,
                   weaknesses: list[str] | None = None,
                   style_tags: list[str] | None = None, level: str = "") -> str:
    """更新用户的写作画像，可单独设置强弱项、风格标签或能力等级。
    通常在评估完成后调用，将评估洞察持久化到用户档案中。"""
    profile = get_profile(user_id)
    if strengths is not None:
        profile.strengths = strengths
    if weaknesses is not None:
        profile.weaknesses = weaknesses
    if style_tags is not None:
        profile.style_tags = style_tags
    if level:
        profile.level = level
    save_profile(profile)
    return f"用户 {user_id} 的写作画像已更新。"


@tool
def create_exercise(topic: str, difficulty: str = "auto",
                    exercise_type: str = "correction", user_id: str = "") -> str:
    """根据指定主题和难度生成一道写作练习题。题型可选：fill_blank（填空）、
    correction（改错）、rewrite（改写）、multiple_choice（选择）。
    difficulty='auto' 时根据用户历史正确率自动调整：>80%升档、<50%降档。
    返回题目内容和唯一 exercise_id，需保留该 id 以供后续批改。"""
    if not is_available():
        return "LLM 不可用，无法生成练习。"
    if difficulty == "auto" and user_id:
        difficulty = _resolve_auto_difficulty(user_id, topic)
    ex = gen_ex(topic=topic, difficulty=difficulty, exercise_type=exercise_type)
    ex_id = str(uuid.uuid4())[:8]
    _pending_exercises[ex_id] = ex
    if user_id:
        try:
            from app.memory.store import save_exercise as db_save_ex
            db_save_ex(ex_id, user_id, json.dumps(ex.model_dump(), default=str, ensure_ascii=False))
        except Exception:
            pass
    return json.dumps({
        "exercise_id": ex_id,
        "type": ex.type, "topic": ex.topic, "question": ex.question,
        "options": ex.options, "difficulty": ex.difficulty,
    }, ensure_ascii=False, indent=2)


@tool
def review_answer(exercise_id: str, user_answer: str, user_id: str = "") -> str:
    """批改用户对练习题的答案，返回正误判断、详细解析和学习建议。
    需要提供 create_exercise 返回的 exercise_id 和用户的作答内容。
    批改结果会自动记录到用户的学习档案中用于追踪进度。"""
    if not is_available():
        return "LLM 不可用，无法批改。"
    ex = _pending_exercises.get(exercise_id)
    if ex is None:
        # 尝试从历史中查找
        return json.dumps({"error": f"找不到练习 {exercise_id}，请重新生成"})
    review = review_ex(ex, user_answer)

    # 记录结果用于难度自适应
    if user_id:
        try:
            record_exercise_result(
                user_id=user_id, topic=ex.topic,
                difficulty=ex.difficulty, is_correct=review.is_correct,
            )
        except Exception:
            pass

    return json.dumps({
        "is_correct": review.is_correct,
        "explanation": review.explanation,
        "suggestion": review.suggestion,
        "difficulty": ex.difficulty,
    }, ensure_ascii=False, indent=2)


@tool
def get_user_history(user_id: str, history_type: str = "evaluation") -> str:
    """查询用户的历史记录。history_type='evaluation' 返回最近 5 次评估记录，
    history_type='exercise' 返回最近 10 次练习记录。用于分析进步轨迹。"""
    if history_type == "evaluation":
        records = get_evaluation_history(user_id, limit=5)
    else:
        records = get_exercise_history(user_id, limit=10)
    if not records:
        return f"暂无{history_type}记录。"
    return json.dumps(records, ensure_ascii=False, default=str, indent=2)


# ═══════════════════════════════════════════════════════════════
# 学习规划与任务追踪
# ═══════════════════════════════════════════════════════════════

@tool
def create_learning_plan(user_id: str, goal: str = "提升整体写作水平",
                         duration: str = "4周") -> str:
    """根据用户写作画像和个性化目标生成结构化学习计划。
    计划包含分步骤主题、每步选择理由、具体学习建议和估计完成时间。
    生成的计划会拆解为独立任务持久化到数据库，可后续追踪进度。"""
    if not is_available():
        return "LLM 不可用，无法生成计划。"

    profile = get_profile(user_id)
    profile_json = json.dumps(profile.model_dump(), ensure_ascii=False, indent=2)

    from app.prompts import PromptRegistry
    from app.services.llm_service import get_llm

    prompt = PromptRegistry.get("LEARNING_PLAN_PROMPT",
                                profile_json=profile_json, goal=goal, duration=duration)
    llm = get_llm(temperature=0.3).bind(response_format={"type": "json_object"})
    resp = llm.invoke(prompt)
    raw = str(resp.content) if hasattr(resp, "content") else str(resp)

    # 解析 JSON
    import re
    text = raw.strip()
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            plan_data = json.loads(m.group())
        except json.JSONDecodeError:
            return json.dumps({"error": "生成计划解析失败，请重试"})
    else:
        return json.dumps({"error": "生成计划格式异常，请重试"})

    plan_id = f"plan-{uuid.uuid4().hex[:8]}"

    # 保存计划
    save_learning_plan(plan_id, user_id, plan_data)

    # 拆解为独立任务
    tasks = []
    for step in plan_data.get("steps", []):
        task = LearningTask(
            task_id=f"task-{uuid.uuid4().hex[:8]}",
            plan_id=plan_id,
            user_id=user_id,
            step_number=step.get("step", 0),
            topic=step.get("topic", ""),
            description=step.get("suggestion", ""),
            status="pending",
            created_at="",
        )
        save_task(task)
        tasks.append(task.model_dump())

    return json.dumps({
        "plan_id": plan_id,
        "goal": plan_data.get("goal", goal),
        "estimated_time": plan_data.get("estimated_time", duration),
        "total_steps": len(tasks),
        "steps": [{
            "task_id": t["task_id"],
            "step": t["step_number"],
            "topic": t["topic"],
            "description": t["description"],
            "status": t["status"],
        } for t in tasks],
    }, ensure_ascii=False, indent=2)


@tool
def get_my_tasks(user_id: str, status: str = "") -> str:
    """查询用户的学习任务列表。可按状态过滤。
    status 可选: 'pending'（未开始）、'in_progress'（进行中）、'completed'（已完成）。
    留空则返回全部任务。返回任务 ID、主题、描述、状态和创建时间。"""
    tasks = get_user_tasks(user_id, status=status if status else "")
    if not tasks:
        return f"{'暂无' + status + '状态的任务' if status else '暂无学习任务'}。"
    return json.dumps(tasks, ensure_ascii=False, default=str, indent=2)


@tool
def update_task_status(task_id: str, status: str) -> str:
    """更新学习任务的状态。status 可选: 'pending'/'in_progress'/'completed'。
    标记完成后自动记录完成时间，并返回整个计划的进度统计。"""
    valid = {"pending", "in_progress", "completed"}
    if status not in valid:
        return f"无效状态 '{status}'，可选: {', '.join(sorted(valid))}"
    ok = store_update_task(task_id, status)
    if not ok:
        return f"未找到任务 {task_id}，请检查 task_id 是否正确。"

    # 更新后获取计划进度
    try:
        from app.memory.store import get_plan_progress, get_user_tasks
        # 从任务列表中反查 plan_id
        tasks = get_user_tasks("")  # 空的 user_id 不会匹配任何用户，先获取所有
        # 其实需要从 task_id 找到 plan_id。先简化：遍历用户任务
        import sqlite3
        from app.memory.store import _conn
        with _conn() as db:
            row = db.execute("SELECT plan_id FROM learning_tasks WHERE task_id = ?", (task_id,)).fetchone()
            if row:
                plan_id = row["plan_id"]
                progress = get_plan_progress(plan_id)
                return json.dumps({
                    "task_id": task_id, "status": status,
                    "plan_progress": progress,
                }, ensure_ascii=False, indent=2)
    except Exception:
        pass
    return f"任务 {task_id} 状态已更新为 '{status}'。"


@tool
def get_task_progress(plan_id: str) -> str:
    """查看学习计划的整体进度。
    返回各状态任务数量、完成总数和完成百分比。"""
    progress = get_plan_progress(plan_id)
    if progress["total"] == 0:
        return f"未找到计划 {plan_id} 或计划下无任务。"
    return json.dumps(progress, ensure_ascii=False, indent=2)


# ═══════════════════════════════════════════════════════════════
# 知识库检索（RAG）
# ═══════════════════════════════════════════════════════════════

@tool
def search_knowledge(query: str) -> str:
    """在写作知识库中搜索相关范文、教程或写作技巧。
    当用户需要参考优秀范例、学习方法、特定体裁写作指导时调用。
    返回语义最匹配的 k 篇相关文档。若知识库为空，返回提示信息。"""
    try:
        from app.services.vector_store import get_vector_store
        store = get_vector_store()
        if store.count() == 0:
            return "知识库暂未收录范文和教程，无法搜索。可通过知识库管理接口添加内容。"
        docs, metas, distances = store.search(query)
        if not docs:
            return "未找到与查询相关的知识内容。"
        results = [{
            "relevance": round(d, 4) if isinstance(d, float) else 0,
            "content": doc[:500],
            "source": meta.get("source", "") if meta else "",
        } for doc, meta, d in zip(docs, metas, distances)]
        return json.dumps({"query": query, "count": len(results), "results": results},
                          ensure_ascii=False, indent=2)
    except Exception as e:
        return f"知识库搜索失败: {e}。检查向量数据库是否正常运行。"


# ═══════════════════════════════════════════════════════════════
# 工具列表
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
# 作品翻阅
# ═══════════════════════════════════════════════════════════════

@tool
def get_my_works(user_id: str, limit: int = 5) -> str:
    """查看学生最近写过的文章列表（标题、字数、日期）。
    用于回顾写作历史、了解学生水平、在教学中引用学生的旧作进行对比和诊断。
    不返回正文内容——要读某一篇的全文请用 read_work 工具。"""
    works = get_user_works(user_id, limit=limit)
    if not works:
        return "还没有保存的写作作品。在使用评估或分析功能时会自动保存。"
    return json.dumps(works, ensure_ascii=False, default=str, indent=2)


@tool
def read_work(work_id: str) -> str:
    """读取学生某一篇作品的全文内容。
    需要提供 work_id（可从 get_my_works 返回的列表中获取）。
    用于查看具体文章内容、分析写作风格、引用原文作为教学素材。"""
    work = get_work_detail(work_id)
    if not work:
        return json.dumps({"error": f"找不到作品 {work_id}"})
    return json.dumps({
        "work_id": work["work_id"],
        "title": work["title"],
        "word_count": work["word_count"],
        "content": work["content"][:3000],
        "created_at": str(work.get("created_at", "")),
        "truncated": len(work["content"]) > 3000,
    }, ensure_ascii=False, indent=2)


ALL_TOOLS = [
    check_rules,
    deep_evaluate,
    get_writing_profile,
    update_profile,
    create_exercise,
    review_answer,
    get_user_history,
    create_learning_plan,
    get_my_tasks,
    update_task_status,
    get_task_progress,
    search_knowledge,
    get_my_works,
    read_work,
]
