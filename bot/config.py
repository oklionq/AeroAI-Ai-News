import os
import sys
import json
from pydantic import BaseModel, Field, ValidationError

class Settings(BaseModel):
    telegram_bot_token: str
    admin_chat_id: int
    target_group_id: int
    default_topic_id: int
    topic_mapping: dict[str, int]
    
    openai_api_key: str
    filter_model: str = "gpt-4o-mini"
    generation_model: str = "gpt-4o-mini"
    max_budget_usd: float = 5.0
    
    poll_interval_minutes: int = 15
    database_url: str = ""
    max_news_age_hours: int = 72
    topic_dedup_days: int = 14
    display_timezone: str = "Europe/Riga"

def load_config() -> Settings:
    from dotenv import load_dotenv
    load_dotenv()
    
    try:
        topic_mapping_str = os.getenv("TOPIC_MAPPING", "{}")
        topic_mapping = json.loads(topic_mapping_str)
        
        settings = Settings(
            telegram_bot_token=os.environ["TELEGRAM_BOT_TOKEN"],
            admin_chat_id=int(os.environ["ADMIN_CHAT_ID"]),
            target_group_id=int(os.environ["TARGET_GROUP_ID"]),
            default_topic_id=int(os.getenv("DEFAULT_TOPIC_ID", "13")),
            topic_mapping=topic_mapping,
            openai_api_key=os.environ["OPENAI_API_KEY"],
            filter_model=os.getenv("FILTER_MODEL", "gpt-4o-mini"),
            generation_model=os.getenv("GENERATION_MODEL", "gpt-4o-mini"),
            max_budget_usd=float(os.getenv("MAX_BUDGET_USD", "5.0")),
            poll_interval_minutes=int(os.getenv("POLL_INTERVAL_MINUTES", "15")),
            database_url=os.getenv("DATABASE_URL", ""),
            max_news_age_hours=int(os.getenv("MAX_NEWS_AGE_HOURS", "72")),
            topic_dedup_days=int(os.getenv("TOPIC_DEDUP_DAYS", "14")),
            display_timezone=os.getenv("DISPLAY_TIMEZONE", "Europe/Riga")
        )
        return settings
    except KeyError as e:
        print(f"CRITICAL ERROR: Missing required environment variable: {e}")
        sys.exit(1)
    except ValueError as e:
        print(f"CRITICAL ERROR: Invalid environment variable type: {e}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"CRITICAL ERROR: Invalid JSON in TOPIC_MAPPING: {e}")
        sys.exit(1)
    except ValidationError as e:
        print(f"CRITICAL ERROR: Configuration validation failed:\n{e}")
        sys.exit(1)

config = load_config()
