from pydantic import BaseSettings

class Settings(BaseSettings):
    # Alpaca交易配置
    alpaca_api_key: str
    alpaca_secret_key: str
    alpaca_base_url: str = "https://paper-api.alpaca.markets"
    
    # 数据库配置（可选）
    db_url: str = "sqlite:///./trading.db"
    
    # 策略配置
    default_strategy: str = "dual_moving_avg"
    risk_tolerance: float = 0.02  # 2%风险容忍度
    
    class Config:
        env_file = ".env"

settings = Settings()




