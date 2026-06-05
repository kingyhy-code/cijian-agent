"""用户写作画像与学习记录持久化 —— SQLite。"""

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from app.models.schemas import WritingProfile, EvalDimension, Exercise, LearningTask

DB_PATH = Path(__file__).parent.parent.parent / "data" / "agent.db"


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """初始化数据库表。"""
    with _conn() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS profiles (
                user_id TEXT PRIMARY KEY,
                data TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS evaluation_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                text_snippet TEXT,
                scores_json TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS pending_exercises (
                exercise_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                exercise_json TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                user_answer TEXT,
                review_json TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                completed_at DATETIME
            );
            CREATE TABLE IF NOT EXISTS exercise_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                exercise_json TEXT NOT NULL,
                user_answer TEXT,
                review_json TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS agent_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS exercise_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                topic TEXT DEFAULT '',
                difficulty TEXT DEFAULT '中等',
                is_correct INTEGER DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS learning_tasks (
                task_id TEXT PRIMARY KEY,
                plan_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                step_number INTEGER,
                topic TEXT DEFAULT '',
                description TEXT DEFAULT '',
                status TEXT DEFAULT 'pending',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                completed_at DATETIME
            );
            CREATE TABLE IF NOT EXISTS learning_plans (
                plan_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                plan_data TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS user_works (
                work_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                title TEXT DEFAULT '',
                content TEXT NOT NULL,
                word_count INTEGER DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
        """)


# ── 写作画像 ───────────────────────────────────────────

def get_profile(user_id: str) -> WritingProfile:
    with _conn() as db:
        row = db.execute("SELECT data FROM profiles WHERE user_id = ?", (user_id,)).fetchone()
    if row:
        return WritingProfile.model_validate(json.loads(row["data"]))
    return WritingProfile(user_id=user_id)


def save_profile(profile: WritingProfile) -> None:
    data = profile.model_dump_json()
    with _conn() as db:
        db.execute(
            "INSERT OR REPLACE INTO profiles (user_id, data) VALUES (?, ?)",
            (profile.user_id, data),
        )


def update_skill_scores(user_id: str, dimensions: list[EvalDimension]) -> None:
    profile = get_profile(user_id)
    for d in dimensions:
        old = profile.skill_scores.get(d.name, 0)
        # 加权滑动平均：70% 保留旧值，30% 新值
        profile.skill_scores[d.name] = int(old * 0.7 + d.score * 0.3)
    profile.total_writings += 1
    save_profile(profile)


# ── 评估历史 ───────────────────────────────────────────

def save_evaluation(user_id: str, text: str, dimensions: list[EvalDimension]) -> None:
    scores = json.dumps([d.model_dump() for d in dimensions], ensure_ascii=False)
    with _conn() as db:
        db.execute(
            "INSERT INTO evaluation_history (user_id, text_snippet, scores_json) VALUES (?, ?, ?)",
            (user_id, text[:500], scores),
        )


def get_evaluation_history(user_id: str, limit: int = 5) -> list[dict[str, Any]]:
    with _conn() as db:
        rows = db.execute(
            "SELECT * FROM evaluation_history WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


# ── 练习历史 ───────────────────────────────────────────

def save_exercise(user_id: str, exercise: Exercise, user_answer: str = "",
                  review: dict | None = None) -> int:
    ex_json = exercise.model_dump_json()
    review_json = json.dumps(review, ensure_ascii=False) if review else None
    with _conn() as db:
        cur = db.execute(
            "INSERT INTO exercise_history (user_id, exercise_json, user_answer, review_json) VALUES (?, ?, ?, ?)",
            (user_id, ex_json, user_answer, review_json),
        )
        return cur.lastrowid


def get_exercise_history(user_id: str, limit: int = 10) -> list[dict[str, Any]]:
    with _conn() as db:
        rows = db.execute(
            "SELECT * FROM exercise_history WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


# ── 对话历史 ───────────────────────────────────────────

def save_message(session_id: str, user_id: str, role: str, content: str) -> None:
    with _conn() as db:
        db.execute(
            "INSERT INTO agent_messages (session_id, user_id, role, content) VALUES (?, ?, ?, ?)",
            (session_id, user_id, role, content),
        )


def get_messages(session_id: str, limit: int = 20) -> list[dict[str, Any]]:
    with _conn() as db:
        rows = db.execute(
            "SELECT role, content FROM agent_messages WHERE session_id = ? ORDER BY id DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


# ── 练习结果追踪（难度自适应）───────────────────────────

def record_exercise_result(user_id: str, topic: str = "",
                           difficulty: str = "中等", is_correct: bool = False) -> None:
    with _conn() as db:
        db.execute(
            "INSERT INTO exercise_results (user_id, topic, difficulty, is_correct) VALUES (?, ?, ?, ?)",
            (user_id, topic, difficulty, 1 if is_correct else 0),
        )


def get_user_accuracy(user_id: str, topic: str | None = None) -> dict[str, Any]:
    """返回用户的练习正确率统计。"""
    with _conn() as db:
        params: list = [user_id]
        topic_filter = ""
        if topic:
            topic_filter = "AND topic = ?"
            params.append(topic)

        row = db.execute(
            f"SELECT COUNT(*) as total, SUM(is_correct) as correct FROM exercise_results WHERE user_id = ? {topic_filter}",
            params,
        ).fetchone()

        recent = db.execute(
            f"SELECT SUM(is_correct) as correct, COUNT(*) as total FROM (SELECT is_correct FROM exercise_results WHERE user_id = ? {topic_filter} ORDER BY created_at DESC LIMIT 5)",
            params,
        ).fetchone()

        rows = db.execute(
            "SELECT difficulty, COUNT(*) as cnt FROM exercise_results WHERE user_id = ? GROUP BY difficulty",
            [user_id],
        ).fetchall()

    total = row["total"] or 0
    correct = row["correct"] or 0
    recent_total = recent["total"] or 0
    recent_correct = recent["correct"] or 0

    return {
        "overall_accuracy": round(correct / total * 100, 1) if total > 0 else 0,
        "total_exercises": total,
        "correct_count": correct,
        "recent_5_accuracy": round(recent_correct / recent_total * 100, 1) if recent_total > 0 else 0,
        "by_difficulty": {r["difficulty"]: r["cnt"] for r in rows},
    }


def get_exercise_results(user_id: str, limit: int = 20) -> list[dict[str, Any]]:
    with _conn() as db:
        rows = db.execute(
            "SELECT * FROM exercise_results WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


# ── 学习计划 ───────────────────────────────────────────

def save_learning_plan(plan_id: str, user_id: str, plan_data: dict) -> None:
    with _conn() as db:
        db.execute(
            "INSERT OR REPLACE INTO learning_plans (plan_id, user_id, plan_data) VALUES (?, ?, ?)",
            (plan_id, user_id, json.dumps(plan_data, ensure_ascii=False)),
        )


def get_learning_plan(plan_id: str) -> dict | None:
    with _conn() as db:
        row = db.execute("SELECT plan_data FROM learning_plans WHERE plan_id = ?", (plan_id,)).fetchone()
    if row:
        return json.loads(row["plan_data"])
    return None


# ── 学习任务 ───────────────────────────────────────────

def save_task(task: LearningTask) -> None:
    with _conn() as db:
        db.execute(
            "INSERT OR REPLACE INTO learning_tasks (task_id, plan_id, user_id, step_number, topic, description, status, completed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (task.task_id, task.plan_id, task.user_id, task.step_number,
             task.topic, task.description, task.status, task.completed_at),
        )


def get_user_tasks(user_id: str, status: str = "") -> list[dict[str, Any]]:
    with _conn() as db:
        if status:
            rows = db.execute(
                "SELECT * FROM learning_tasks WHERE user_id = ? AND status = ? ORDER BY step_number",
                (user_id, status),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM learning_tasks WHERE user_id = ? ORDER BY step_number",
                (user_id,),
            ).fetchall()
    return [dict(r) for r in rows]


def update_task_status(task_id: str, status: str) -> bool:
    with _conn() as db:
        cur = db.execute(
            "UPDATE learning_tasks SET status = ?, completed_at = ? WHERE task_id = ?",
            (status, "CURRENT_TIMESTAMP" if status == "completed" else None, task_id),
        )
        return cur.rowcount > 0


def get_latest_plan_id(user_id: str) -> str | None:
    with _conn() as db:
        row = db.execute(
            "SELECT plan_id FROM learning_plans WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()
    return row["plan_id"] if row else None

def get_plan_progress(plan_id: str) -> dict[str, Any]:
    with _conn() as db:
        row = db.execute(
            "SELECT COUNT(*) as total, SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) as completed, SUM(CASE WHEN status='in_progress' THEN 1 ELSE 0 END) as in_progress, SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) as pending FROM learning_tasks WHERE plan_id = ?",
            (plan_id,),
        ).fetchone()
    total = row["total"] or 0
    completed = row["completed"] or 0
    return {
        "total": total,
        "completed": completed,
        "in_progress": row["in_progress"] or 0,
        "pending": row["pending"] or 0,
        "percentage": round(completed / total * 100, 1) if total > 0 else 0,
    }


# ── 用户作品 ───────────────────────────────────────────

def save_work(user_id: str, content: str, title: str = "") -> str:
    """保存用户写作作品全文。返回 work_id。"""
    import uuid as _uuid
    work_id = f"work-{_uuid.uuid4().hex[:8]}"
    word_count = len(content.replace(" ", "").replace("\n", ""))
    with _conn() as db:
        db.execute(
            "INSERT INTO user_works (work_id, user_id, title, content, word_count) VALUES (?, ?, ?, ?, ?)",
            (work_id, user_id, title or "无标题", content, word_count),
        )
    return work_id


def get_user_works(user_id: str, limit: int = 10) -> list[dict[str, Any]]:
    """获取用户写作作品列表（最近优先）。"""
    with _conn() as db:
        rows = db.execute(
            "SELECT work_id, title, word_count, created_at FROM user_works WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def get_work_detail(work_id: str) -> dict | None:
    """获取单篇作品全文。"""
    with _conn() as db:
        row = db.execute("SELECT * FROM user_works WHERE work_id = ?", (work_id,)).fetchone()
    if row:
        return dict(row)
    return None


def get_cumulative_stats(user_id: str) -> dict[str, Any]:
    """统计累计字数、学习天数。"""
    with _conn() as db:
        words_row = db.execute(
            "SELECT COALESCE(SUM(word_count), 0) as total_words FROM user_works WHERE user_id = ?",
            (user_id,),
        ).fetchone()

        days_row = db.execute(
            "SELECT MIN(created_at) as first_date FROM evaluation_history WHERE user_id = ?",
            (user_id,),
        ).fetchone()

    from datetime import datetime, date
    total_words = words_row["total_words"] or 0
    learning_days = 0
    if days_row["first_date"]:
        first = datetime.strptime(days_row["first_date"][:10], "%Y-%m-%d").date()
        learning_days = (date.today() - first).days

    badges = _calculate_badges(total_words, learning_days)
    return {
        "cumulative_words": total_words,
        "learning_days": learning_days,
        "badges": badges,
    }


def _calculate_badges(words: int, days: int) -> list[dict]:
    """根据字数和天数计算徽章。"""
    badges = []
    if words >= 500:
        badges.append({"id": "first_500", "name": "初露锋芒", "icon": "✒️",
                        "desc": "累计写作 500 字", "earned": True})
    if words >= 2000:
        badges.append({"id": "writer_2k", "name": "笔耕不辍", "icon": "📝",
                        "desc": "累计写作 2000 字", "earned": True})
    if words >= 5000:
        badges.append({"id": "writer_5k", "name": "妙笔生花", "icon": "🌸",
                        "desc": "累计写作 5000 字", "earned": True})
    if words >= 10000:
        badges.append({"id": "writer_10k", "name": "下笔有神", "icon": "🏆",
                        "desc": "累计写作 10000 字", "earned": True})
    if words >= 20000:
        badges.append({"id": "writer_20k", "name": "著作等身", "icon": "👑",
                        "desc": "累计写作 20000 字", "earned": True})
    if days >= 7:
        badges.append({"id": "week_learner", "name": "坚持一周", "icon": "🔥",
                        "desc": "持续学习 7 天", "earned": True})
    if days >= 30:
        badges.append({"id": "month_learner", "name": "月度学员", "icon": "📅",
                        "desc": "持续学习 30 天", "earned": True})
    return badges


def get_skill_score_history(user_id: str, limit: int = 20) -> list[dict[str, Any]]:
    """获取能力分数历史（从 evaluation_history 中提取，用于进度图表）"""
    with _conn() as db:
        rows = db.execute(
            "SELECT scores_json, created_at FROM evaluation_history WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    history = []
    for r in reversed(rows):
        try:
            scores = json.loads(r["scores_json"])
            for dim in scores:
                history.append({
                    "date": r["created_at"][:10] if r["created_at"] else "",
                    "dimension": dim.get("name", ""),
                    "score": dim.get("score", 0),
                })
        except (json.JSONDecodeError, TypeError):
            continue
    return history


# ── 练习题持久化 ───────────────────────────────────────────

def save_exercise(exercise_id: str, user_id: str, exercise_json: str) -> None:
    with _conn() as db:
        db.execute(
            "INSERT OR REPLACE INTO pending_exercises (exercise_id, user_id, exercise_json, status) VALUES (?, ?, ?, 'pending')",
            (exercise_id, user_id, exercise_json),
        )

def get_user_exercises(user_id: str, status: str = '') -> list[dict[str, Any]]:
    with _conn() as db:
        if status:
            rows = db.execute(
                "SELECT * FROM pending_exercises WHERE user_id = ? AND status = ? ORDER BY created_at DESC",
                (user_id, status),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM pending_exercises WHERE user_id = ? ORDER BY created_at DESC",
                (user_id,),
            ).fetchall()
    result = []
    for r in rows:
        ex = json.loads(r["exercise_json"])
        review = json.loads(r["review_json"]) if r["review_json"] else None
        result.append({
            "exercise_id": r["exercise_id"], "user_id": r["user_id"],
            "type": ex.get("type", ""), "topic": ex.get("topic", ""), "question": ex.get("question", ""),
            "options": ex.get("options"), "answer": ex.get("answer", ""),
            "difficulty": ex.get("difficulty", ""),
            "status": r["status"], "user_answer": r["user_answer"],
            "review": review, "created_at": r["created_at"],
        })
    return result

def submit_exercise_answer(exercise_id: str, user_answer: str, review_json: str) -> bool:
    with _conn() as db:
        cur = db.execute(
            "UPDATE pending_exercises SET user_answer = ?, review_json = ?, status = 'completed', completed_at = CURRENT_TIMESTAMP WHERE exercise_id = ?",
            (user_answer, review_json, exercise_id),
        )
        return cur.rowcount > 0

def get_exercise_by_id(exercise_id: str) -> dict[str, Any] | None:
    with _conn() as db:
        r = db.execute("SELECT * FROM pending_exercises WHERE exercise_id = ?", (exercise_id,)).fetchone()
    if not r:
        return None
    ex = json.loads(r["exercise_json"])
    review = json.loads(r["review_json"]) if r["review_json"] else None
    return {
        "exercise_id": r["exercise_id"], "user_id": r["user_id"],
        "type": ex.get("type", ""), "topic": ex.get("topic", ""), "question": ex.get("question", ""),
        "options": ex.get("options"), "answer": ex.get("answer", ""),
        "difficulty": ex.get("difficulty", ""),
        "status": r["status"], "user_answer": r["user_answer"],
        "review": review, "created_at": r["created_at"],
    }
