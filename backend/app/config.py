"""应用配置：所有可调项集中于此，通过环境变量 / .env 注入。"""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # 配置统一从 backend/.env 读取（run.bat 每次启动会把根目录 .env 同步为同一份）
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # 应用安全
    app_password: str = "change-me"
    secret_key: str = "dev-insecure-secret-change-me"
    cookie_secure: bool = False
    session_ttl_hours: int = 72

    # 存储
    data_dir: str = "./data"

    # 大模型
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"
    llm_timeout: int = 180

    # ---- 语音转写层（层 3）----
    # provider: "tencent"（腾讯云录音文件识别极速版，推荐）或 "openai"（OpenAI/Groq 兼容）
    asr_provider: str = "tencent"
    asr_timeout: int = 300

    # 腾讯云 ASR（provider=tencent 时使用；三项缺一即跳过 ASR 层）
    tencent_appid: str = ""
    tencent_secret_id: str = ""
    tencent_secret_key: str = ""
    tencent_engine_type: str = "16k_zh"   # 中文通用引擎
    tencent_voice_format: str = "m4a"     # B 站 dash 音频流通常为 m4a

    # OpenAI/Groq 兼容 ASR（provider=openai 时使用；留空则跳过）
    asr_base_url: str = "https://api.groq.com/openai/v1"
    asr_api_key: str = ""
    asr_model: str = "whisper-large-v3"

    # 长文本分块（map-reduce）：块大小（字符）与并发
    chunk_size: int = 4000
    chunk_overlap: int = 200
    map_concurrency: int = 3

    # 任务
    max_concurrent_tasks: int = 2

    # CORS
    cors_origins: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
