# cijian-agent v2.0

真正的 AI Agent 写作教练，基于 LangGraph 的 ReAct 循环实现。

## 和 v1.0 的区别

| | v1.0（假 Agent） | v2.0（真 Agent） |
|---|---|---|
| 决策者 | 代码硬编码流程 | LLM 自主决策 |
| 工具调用 | 无 | LLM 主动选择和调用工具 |
| 循环 | 请求→响应一次结束 | LLM 循环：思考→调工具→观察→再思考 |
| 入口 | 多个 REST 端点 | 统一 /agent/chat |
| 记忆 | 无状态 | SQLite 持久化用户画像和学习记录 |

## 技术栈

LangGraph + LangChain + FastAPI + SQLite

## 启动

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

## API

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /agent/chat | 统一 Agent 入口 |
| GET | /agent/history/{session_id} | 会话历史 |
| GET | /health | 健康检查 |
