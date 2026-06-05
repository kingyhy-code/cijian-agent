# 辞间 Agent 服务 (cijian-agent)

基于 LangGraph 的 AI 写作教练智能体。辞间平台的全部 AI 能力集中于此服务。LLM 自主决策、调用工具、循环推理的 ReAct Agent。

## 架构

```
FastAPI (:8000)
├── /agent/chat              → LangGraph Agent 对话（LLM 自主推理 + 工具调用）
├── /agent/chat/stream       → SSE 流式对话（边推理边返回）
├── /agent/coach/*           → 创作教练（分析/评估/润色/帮写/对话）
├── /agent/companion/*       → 阅读伴侣（文学分析/导读/对话/检验）
├── /agent/progress/*        → 进度可视化（分数历史/练习统计/概览）
├── /agent/profile/{id}      → 用户学习档案
├── /agent/tasks/{id}        → 学习任务列表
├── /agent/plan/create       → 直接创建学习计划
├── /agent/knowledge/*       → 知识库 RAG（搜索/添加/导入）
└── /agent/health            → 健康检查

Agent 内部
├── LangGraph 状态图（agent_node ⇄ tool_executor）
├── 14 个注册工具（规则检测/深度评估/用户画像/学习计划/练习生成/批改/进度追踪/知识检索）
├── SQLite 持久化（写作画像/评估历史/练习记录/对话历史/学习计划/任务）
├── ChromaDB 向量检索（写作知识库 RAG）
└── LLM 客户端（OpenAI 兼容 API，对接千问/DeepSeek）
```

## 目录结构

```
D:\CijianAgent\
├── app/
│   ├── main.py                  # FastAPI 入口，20+ REST 端点
│   ├── config.py                # 环境配置（AI_API_KEY / 模型 / 端口 / Agent 安全边界）
│   ├── agent/
│   │   ├── graph.py             # LangGraph 状态图 + 教学状态机 + ReAct 循环
│   │   ├── tools.py             # 14 个工具注册（@tool 装饰）
│   │   └── executor.py          # 统一执行框架（生命周期/错误处理/画像联动）
│   ├── tools/
│   │   ├── rules.py             # L1-L3 规则引擎（60+ 检测项，纯正则毫秒级）
│   │   ├── prompts.py           # LLM Prompt 模板（评估/润色/帮写/文学分析/导读等）
│   │   └── exercise.py          # 练习生成 + 批改
│   ├── memory/
│   │   └── store.py             # SQLite 持久化（画像/评估/练习/对话/计划/任务）
│   ├── models/
│   │   └── schemas.py           # Pydantic 数据模型
│   ├── prompts/
│   │   └── registry.py          # Prompt 模板注册表
│   └── services/
│       ├── llm_service.py       # LLM 客户端（ChatOpenAI + httpx SSL 绕过）
│       ├── llm_adapter.py       # 多模型适配器（千问 DeepSeek）
│       └── vector_store.py      # ChromaDB 向量存储封装
├── data/
│   ├── agent.db                 # SQLite 数据库（运行时生成）
│   ├── chroma_db/               # ChromaDB 持久化目录
│   ├── seed_knowledge.py        # 种子写作知识
│   └── gaokao_knowledge.py      # 高考作文素材
├── static/index.html            # Agent 测试页面
├── requirements.txt
├── .env                         # API Key 配置
├── README.md
└── CLAUDE.md
```

## API 端点

### Agent 对话
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/agent/chat` | Agent 对话入口：LLM 自动判断意图，调用工具，综合结果回复 |
| POST | `/agent/chat/stream` | SSE 流式对话 |
| GET | `/agent/history/{sessionId}` | 获取会话历史 |
| GET | `/agent/health` | 健康检查 |

**Agent 对话响应格式：**
```json
{
  "reply": "回复内容...",
  "session_id": "sess-abc123",
  "tool_calls_made": ["get_writing_profile", "create_learning_plan"]
}
```

### 创作教练
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/agent/coach/analyze` | L1-L3 规则检测（毫秒级，零成本） |
| POST | `/agent/coach/evaluate` | 五维度深度评估（逻辑/词汇/情感/结构/简洁） |
| POST | `/agent/coach/polish` | 分级润色（light/medium/heavy） |
| POST | `/agent/coach/inspire` | 帮写（灵感激发 / 定向续写） |
| POST | `/agent/coach/chat` | 基于作品全文的教练自由对话 |

### 阅读伴侣
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/agent/companion/analyze` | 文学手法识别 + 阅读洞察 |
| POST | `/agent/companion/overview` | 宏观导读 |
| POST | `/agent/companion/chat` | 选段多轮文学讨论 |
| POST | `/agent/companion/evaluate` | 检验点评 |
| POST | `/agent/companion/guide/chat` | 全程对话式导读 |

### 学习进度
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/agent/profile/{userId}` | 用户学习档案（字数/天数/徽章） |
| GET | `/agent/tasks/{userId}` | 学习任务列表（左侧面板） |
| POST | `/agent/plan/create` | 直接创建学习计划（{user_id, goal}） |
| GET | `/agent/progress/scores/{userId}` | 五维能力分数历史 |
| GET | `/agent/progress/exercise/{userId}` | 练习统计 |
| GET | `/agent/progress/summary/{userId}` | 综合进度概览 |

### 知识库
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/agent/knowledge/search` | 语义搜索 |
| POST | `/agent/knowledge/add` | 添加文档 |
| POST | `/agent/knowledge/import` | 导入长文本（自动分块） |
| GET | `/agent/knowledge/stats` | 索引统计 |

## Agent 工具列表

| 工具 | 功能 | 实现 |
|------|------|------|
| `check_rules` | L1-L3 规则检测 | 纯正则，毫秒级 |
| `deep_evaluate` | 五维度深度评估 | LLM |
| `get_writing_profile` | 读取用户写作画像 | SQLite |
| `update_profile` | 更新用户强弱项和水平 | SQLite |
| `create_exercise` | 生成个性化练习（支持 auto 难度自适应） | LLM |
| `review_answer` | 批改练习答案（自动记录结果） | LLM |
| `get_user_history` | 查看评估和练习历史 | SQLite |
| `create_learning_plan` | 生成个性化学习计划 + 任务拆解 | LLM + SQLite |
| `get_my_tasks` | 查看学习任务列表 | SQLite |
| `update_task_status` | 更新任务状态 | SQLite |
| `get_task_progress` | 查看计划进度统计 | SQLite |
| `search_knowledge` | 搜索写作范文和技巧 | ChromaDB |

## 快速开始

```bash
pip install -r requirements.txt
cp .env.example .env  # 填入 AI_API_KEY
uvicorn app.main:app --reload --host 0.0.0.0 --port 8001
```

### 环境变量 (`.env`)

| 变量 | 说明 | 示例 |
|------|------|------|
| `AI_API_KEY` | LLM API 密钥 | `sk-xxx` |
| `AI_BASE_URL` | OpenAI 兼容端点 | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| `AI_MODEL` | 模型名 | `qwen-max` |
| `PORT` | 端口 | `8001` |
| `LOG_LEVEL` | 日志级别 | `info` |

### Agent 安全边界

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `agent_max_steps` | 10 | ReAct 循环最大步数 |
| `agent_max_token_estimate` | 32000 | 消息历史最大 token |
| `agent_timeout` | 180 | Agent 执行超时（秒） |
| `tool_result_max_chars` | 4000 | 单个工具结果最大字符数 |

## 教学状态机

Agent 通过代码层状态机强制教学闭环，不依赖 LLM 自觉：
- `get_writing_profile` → 自动提示调用 `create_learning_plan`
- `create_learning_plan` → 生成后强制停止，等待用户确认
- `create_exercise` → 出题后等待答案，用 `review_answer` 批改
- `review_answer` → 根据结果决定升级/补课/继续

## 技术栈

| 层 | 技术 |
|---|---|
| Agent 框架 | LangGraph（ReAct 循环 + 状态图） |
| Tool Calling | LangChain @tool 装饰器 |
| HTTP 框架 | FastAPI + Uvicorn |
| LLM 客户端 | langchain-openai（OpenAI 兼容 API） |
| 向量检索 | ChromaDB |
| 持久化 | SQLite3 |
| 数据模型 | Pydantic 2.x |

## 进度

### 已完成 8 项核心能力

| # | 能力 | 说明 |
|---|------|------|
| 1 | 深度评估与诊断 | 规则引擎 + 五维度评估 + 用户画像 |
| 2 | 个性化学习规划 | create_learning_plan + 任务拆解 + 追踪 |
| 3 | 智能拆解与引导 | 任务拆分树 + SQLite 持久化 + 状态追踪 |
| 4 | 实时指导与反馈 | SSE 流式输出 + 边推理边返回 |
| 5 | 针对性批改与优化 | 练习批改 + 正误判断 + 解析建议 |
| 6 | 动态调整教学策略 | 基于历史正确率自适应调整练习难度 |
| 7 | 进度追踪与激励 | 分数历史/练习统计/里程碑 API |
| 8 | 资源匹配与连接 | ChromaDB 向量检索 + search_knowledge |

### 前端对接

开发模式下前端 Vite 代理直连 Agent（不走 Gateway）：

```
前端 (5173) → /api/ai/* → Agent (8001)
前端 (5173) → /api/agent/* → Agent (8001)
```

---

*最后更新：2026-05-30*
