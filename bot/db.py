import aiosqlite
import os
import json
from datetime import datetime, timezone
from bot.config import config
from bot.utils.logger import logger

DB_PATH = config.database_url if config.database_url else os.path.join("data", "bot.db")

async def init_db():
    if not config.database_url:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    
    async with aiosqlite.connect(DB_PATH) as db:
        # sources table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                type TEXT NOT NULL,
                url TEXT NOT NULL,
                enabled BOOLEAN DEFAULT 1,
                fail_count INTEGER DEFAULT 0,
                last_success_at DATETIME,
                last_error TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # news_items table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS news_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER,
                url TEXT NOT NULL,
                url_hash TEXT NOT NULL,
                title_hash TEXT NOT NULL,
                title TEXT NOT NULL,
                summary TEXT,
                published_at DATETIME,
                collected_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                status TEXT DEFAULT 'collected',
                filter_category TEXT,
                subject TEXT,
                filter_reason TEXT,
                image_url TEXT,
                post_text_json TEXT,
                telegram_message_id INTEGER,
                thread_id INTEGER,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # api_usage table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS api_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                stage TEXT,
                model TEXT,
                input_tokens INTEGER,
                output_tokens INTEGER,
                cost_usd REAL,
                news_item_id INTEGER
            )
        """)
        
        # errors_log table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS errors_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                component TEXT,
                message TEXT,
                news_item_id INTEGER
            )
        """)
        
        # bot_state table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS bot_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                is_paused BOOLEAN DEFAULT 0,
                pause_reason TEXT,
                budget_spent_usd REAL DEFAULT 0.0,
                last_poll_at DATETIME,
                next_poll_at DATETIME,
                started_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # poll_cycles table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS poll_cycles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at DATETIME,
                finished_at DATETIME,
                duration_seconds INTEGER,
                sources_total INTEGER DEFAULT 0,
                sources_ok INTEGER DEFAULT 0,
                sources_failed INTEGER DEFAULT 0,
                items_raw INTEGER DEFAULT 0,
                items_filtered_stale INTEGER DEFAULT 0,
                items_after_dedup INTEGER DEFAULT 0,
                items_passed_filter INTEGER DEFAULT 0,
                items_sent_moderation INTEGER DEFAULT 0,
                items_auto_published INTEGER DEFAULT 0,
                errors_count INTEGER DEFAULT 0,
                last_errors_json TEXT,
                status TEXT
            )
        """)
        
        # Insert initial bot state if not exists
        await db.execute("""
            INSERT OR IGNORE INTO bot_state (id, is_paused, budget_spent_usd) 
            VALUES (1, 0, 0.0)
        """)
        
        # Insert initial sources if table is empty
        async with db.execute("SELECT COUNT(*) FROM sources") as cursor:
            row = await cursor.fetchone()
            if row[0] == 0:
                sources = [
                    ("OpenAI News", "rss", "https://openai.com/news/rss.xml"),
                    ("Hugging Face Blog", "rss", "https://huggingface.co/blog/feed.xml"),
                    ("Google DeepMind Blog", "rss", "https://deepmind.google/blog/rss.xml"), # Placeholder URL
                    ("TechCrunch AI", "rss", "https://techcrunch.com/category/artificial-intelligence/feed/"),
                    ("VentureBeat AI", "rss", "https://venturebeat.com/category/ai/feed/"),
                    ("Reddit Singularity", "reddit", "https://www.reddit.com/r/singularity/.rss"),
                    ("Reddit LocalLLaMA", "reddit", "https://www.reddit.com/r/LocalLLaMA/.rss"),
                    ("Reddit OpenAI", "reddit", "https://www.reddit.com/r/OpenAI/.rss"),
                    ("Hacker News AI", "hackernews", "http://hn.algolia.com/api/v1/search_by_date?query=GPT%20OR%20Claude%20OR%20Gemini%20OR%20Grok%20OR%20OpenAI%20OR%20Anthropic&tags=story")
                ]
                await db.executemany("""
                    INSERT INTO sources (name, type, url) VALUES (?, ?, ?)
                """, sources)
        
        
        # Migrations for new columns
        for col_query in [
            "ALTER TABLE news_items ADD COLUMN decision_at DATETIME",
            "ALTER TABLE news_items ADD COLUMN reject_reason TEXT",
            "ALTER TABLE news_items ADD COLUMN filter_confidence REAL",
            "ALTER TABLE news_items ADD COLUMN auto_published BOOLEAN DEFAULT 0",
            "ALTER TABLE news_items ADD COLUMN subject TEXT",
            "ALTER TABLE sources ADD COLUMN disabled_at DATETIME",
            "ALTER TABLE poll_cycles ADD COLUMN items_filtered_stale INTEGER DEFAULT 0"
        ]:
            try:
                await db.execute(col_query)
            except Exception:
                pass
            
        try:
            await db.execute("ALTER TABLE bot_state ADD COLUMN auto_publish_categories TEXT DEFAULT ''")
        except Exception:
            pass

        await db.commit()

async def log_error(component: str, message: str, news_item_id: int = None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO errors_log (component, message, news_item_id)
            VALUES (?, ?, ?)
        """, (component, message, news_item_id))
        await db.commit()
    logger.error(f"[{component}] {message}")

def get_db_connection():
    return aiosqlite.connect(DB_PATH)
