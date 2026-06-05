# 辞间 AI 写作教练

基于 LangGraph ReAct 循环的智能写作教学 Agent。LLM 自主决策调用 14 个教学工具，实现个性化写作指导、学习计划管理、出题批改、知识库 RAG 等能力。

## 技术栈

Python 3.12 · FastAPI · LangGraph · LangChain · ChromaDB · Qwen-Max (DashScope) · SQLite

## 架构

```
用户消息 → Agent 节点（LLM 推理）↔ Tool Executor（工具执行）
                              ↑
                    最多 10 轮 ReAct 循环

14 个 @tool：规则检测 / 五维评估 / 画像读写 / 出题（4 种题型）/ 批改 / 学习计划 / 任务追踪 / 知识库检索 / 作品查询

教学状态机：
  get_writing_profile → 强制提示创建学习计划
  create_learning_plan → 生成后停止，等待确认
  create_exercise → 等待提交答案后用 review_answer 批改
  review_answer → 根据正确率决定升级/补课
  _auto_advance_task → 每次对话结束自动推进学习进度
```

## 快速启动

```bash
pip install -r requirements.txt
cp .env.example .env   # 填入 AI_API_KEY
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## 主要端点

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /agent/chat | ReAct 对话 |
| POST | /agent/chat/stream | SSE 流式对话 |
| GET | /agent/history/{id} | 会话历史 |
| POST | /agent/coach/analyze | 规则检测（毫秒级） |
| POST | /agent/coach/evaluate | 五维深度评估 |
| POST | /agent/coach/polish | 文本润色 |
| POST | /agent/coach/inspire | 帮写/灵感激发 |
| GET | /agent/tasks/{user_id} | 学习任务列表 |
| GET | /agent/exercises/{user_id} | 练习题列表 |
| POST | /agent/exercise/review | 提交练习答案 |
| GET | /agent/progress/summary/{user_id} | 综合进度 |
| POST | /agent/knowledge/search | 知识库检索 |

## 目录

```
app/
├── main.py           # FastAPI 入口
├── agent/
│   ├── graph.py      # LangGraph 状态图 + 状态机
│   ├── tools.py      # 14 个工具注册
│   └── executor.py   # 执行框架
├── tools/            # 规则引擎 / Prompt / 练习
├── memory/store.py   # SQLite 持久化
├── prompts/          # Prompt 模板注册表
├── services/         # LLM 客户端 / 多模型适配 / 向量存储
├── models/           # Pydantic 数据模型
└── data/             # 种子知识 / 高考素材
```
