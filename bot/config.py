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
    
    # Telethon parser settings
    telethon_api_id: int
    telethon_api_hash: str
    telethon_session_name: str = "parser_session"
    donor_channel: str = "ThePerceptronChannel"
    
    # LLM settings (Venice AI / OpenAI compatible)
    openai_api_key: str
    openai_base_url: str = "https://api.venice.ai/api/v1"
    filter_model: str = "deepseek-v4-1-flash-1017-uncensored"
    generation_model: str = "deepseek-v4-1-flash-1017-uncensored"
    max_budget_usd: float = 5.0
    
    poll_interval_minutes: int = 15
    database_url: str = ""
    max_news_age_hours: int = 72
    topic_dedup_days: int = 14
    display_timezone: str = "Europe/Riga"
    min_image_width: int = 300
    auto_publish: bool = False

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
            telethon_api_id=int(os.getenv("TELETHON_API_ID", os.getenv("API_ID", "0"))),
            telethon_api_hash=os.getenv("TELETHON_API_HASH", os.getenv("API_HASH", "")),
            telethon_session_name=os.getenv("TELETHON_SESSION_NAME", os.getenv("SESSION_NAME", "parser_session")),
            donor_channel=os.getenv("DONOR_CHANNEL", "ThePerceptronChannel"),
            openai_api_key=os.environ["OPENAI_API_KEY"],
            openai_base_url=os.getenv("OPENAI_BASE_URL", "https://api.venice.ai/api/v1"),
            filter_model=os.getenv("FILTER_MODEL", os.getenv("LLM_MODEL", "deepseek-v4-1-flash-1017-uncensored")),
            generation_model=os.getenv("GENERATION_MODEL", os.getenv("LLM_MODEL", "deepseek-v4-1-flash-1017-uncensored")),
            max_budget_usd=float(os.getenv("MAX_BUDGET_USD", "5.0")),
            poll_interval_minutes=int(os.getenv("POLL_INTERVAL_MINUTES", "15")),
            database_url=os.getenv("DATABASE_URL", ""),
            max_news_age_hours=int(os.getenv("MAX_NEWS_AGE_HOURS", "72")),
            topic_dedup_days=int(os.getenv("TOPIC_DEDUP_DAYS", "14")),
            display_timezone=os.getenv("DISPLAY_TIMEZONE", "Europe/Riga"),
            min_image_width=int(os.getenv("MIN_IMAGE_WIDTH", "300")),
            auto_publish=os.getenv("AUTO_PUBLISH", "false").lower() in ("true", "1", "yes")
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
