from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    ai_api_key: str = ""
    ai_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    ai_model: str = "qwen-plus"

    java_ai_url: str = "http://localhost:8085"

    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "info"

    # Agent 安全边界
    agent_max_steps: int = 10          # Agent 最大循环步数（LangGraph recursion_limit）
    agent_max_token_estimate: int = 32000  # 消息历史最大 token 估算值
    agent_timeout: int = 180           # Agent 执行超时（秒）
    tool_result_max_chars: int = 4000  # 单个工具结果最大字符数

    # 知识库（RAG）
    chroma_persist_path: str = "data/chroma_db"   # ChromaDB 持久化路径
    chroma_collection: str = "writing_knowledge"  # 默认集合名

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
