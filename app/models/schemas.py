from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Any


# ── 统一 API 响应 ──────────────────────────────────────────

class ApiResponse(BaseModel):
    """统一的 API 响应格式"""
    code: int = 200
    message: str = "success"
    data: dict[str, Any] | list | None = None
    error: str | None = None


class ErrorDetail(BaseModel):
    """结构化错误信息"""
    type: str = "internal_error"
    detail: str = ""
    retryable: bool = False


# ── Agent 请求/响应 ──────────────────────────────────────────

class AgentRequest(BaseModel):
    user_id: str = "default"
    message: str
    session_id: str | None = None


class AgentResponse(BaseModel):
    reply: str
    session_id: str
    tool_calls_made: list[str] = []


# ── 用户写作画像 ──────────────────────────────────────────

class WritingProfile(BaseModel):
    user_id: str
    strengths: list[str] = []
    weaknesses: list[str] = []
    style_tags: list[str] = []
    level: str = "中级"
    total_writings: int = 0
    skill_scores: dict[str, int] = Field(default_factory=lambda: {
        "逻辑连贯性": 0, "词汇精准度": 0, "情感感染力": 0, "结构节奏": 0, "表达简洁性": 0
    })


# ── 评估结果 ──────────────────────────────────────────────

class EvalDimension(BaseModel):
    name: str
    score: int
    comment: str
    suggestion: str


class EvaluationResult(BaseModel):
    dimensions: list[EvalDimension]
    highlight: str
    improvement: str
    overall_score: int = 0


# ── 练习题 ──────────────────────────────────────────────

class Exercise(BaseModel):
    type: str  # fill_blank, correction, rewrite, multiple_choice
    topic: str
    question: str
    options: list[str] | None = None
    answer: str
    explanation: str
    difficulty: str = "中等"


class ExerciseReview(BaseModel):
    is_correct: bool
    explanation: str
    suggestion: str


# ── 学习计划 ──────────────────────────────────────────────

class LearningStep(BaseModel):
    step: int
    topic: str
    reason: str
    suggestion: str


class LearningPlan(BaseModel):
    goal: str
    steps: list[LearningStep]
    estimated_time: str


# ── 诊断结果 ──────────────────────────────────────────────

class WeaknessItem(BaseModel):
    category: str
    description: str
    severity: int
    example: str


class DiagnosisResult(BaseModel):
    overall_level: str
    weaknesses: list[WeaknessItem]
    learning_path: list[LearningStep]


# ── 学习任务 ──────────────────────────────────────────────

class LearningTask(BaseModel):
    task_id: str
    plan_id: str
    user_id: str
    step_number: int = 0
    topic: str = ""
    description: str = ""
    status: str = "pending"  # pending / in_progress / completed
    created_at: str = ""
    completed_at: str | None = None


# ── 进度可视化 ────────────────────────────────────────────

class SkillProgressPoint(BaseModel):
    date: str
    dimension: str
    score: int


class ExerciseStat(BaseModel):
    total: int = 0
    correct: int = 0
    accuracy: float = 0.0
    by_difficulty: dict[str, int] = Field(default_factory=dict)


class UserProgressSummary(BaseModel):
    user_id: str
    level: str = "中级"
    total_writings: int = 0
    total_exercises: int = 0
    overall_accuracy: float = 0.0
    skill_scores: dict[str, int] = Field(default_factory=dict)
    skill_trend: list[dict] = Field(default_factory=list)
    completed_tasks: int = 0
    total_tasks: int = 0
    recent_activity: list[dict] = Field(default_factory=list)
