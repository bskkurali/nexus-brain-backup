"""AiTrader Pro Settings"""
from pydantic_settings import BaseSettings
from functools import lru_cache
from dotenv import load_dotenv
load_dotenv()

class Settings(BaseSettings):
    execution_mode: str = "paper"
    mt5_mode: str = "paper"
    starting_equity: float = 100.0
    mt5_login: int = 0
    mt5_password: str = ""
    mt5_server: str = "Exness-MT5Trail14"
    anthropic_api_key: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: int = 666806182
    google_ai_api_key: str = ""
    google_ai_api_key_2: str = ""
    ollama_url: str = "http://localhost:11434"
    nvidia_api_key: str = ""
    grok_api_key: str = ""
    max_daily_loss_pct: float = 5.0
    max_weekly_loss_pct: float = 15.0
    max_risk_per_trade_pct: float = 2.0
    max_trades_per_day: int = 5
    dashboard_host: str = "0.0.0.0"
    dashboard_port: int = 8000
    claude_model: str = "claude-sonnet-4-6"
    symbol: str = "XAUUSDm"
    use_gemma: bool = True

    # MT5 / Bridge settings
    mt5_symbol: str = "XAUUSDm"
    mt5_default_lot: float = 0.01
    mt5_max_lot: float = 1.0
    mt5_bridge_url: str = "http://localhost:5000"
    mt5_bridge_token: str = ""

    # Risk settings
    min_rr_ratio: float = 1.5

    # Session times (IST = UTC+5:30)
    session_london_open: str = "11:30"
    session_ny_open: str = "17:30"
    session_overlap_close: str = "21:00"
    session_dead_zone_start: str = "23:00"
    session_dead_zone_end: str = "11:30"

    class Config:
        env_file = ".env"
        extra = "ignore"

    @property
    def is_paper(self) -> bool:
        return self.execution_mode == "paper"

    @property
    def use_mt5_bridge(self) -> bool:
        return self.mt5_mode == "bridge"

@lru_cache()
def get_settings():
    return Settings()

settings = get_settings()
