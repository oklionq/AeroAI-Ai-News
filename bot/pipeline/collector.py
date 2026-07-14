from bot.db import get_db_connection
from bot.utils.dedup import normalize_title, generate_hash, get_url_hash, title_similarity
from bot.sources.rss_sources import fetch_rss
from bot.sources.reddit import fetch_reddit
from bot.sources.hackernews import fetch_hackernews
from bot.utils.logger import logger
from bot.config import config
import asyncio
from datetime import datetime

async def run_collector(tracker=None):
    logger.info("Starting collector cycle.")
    
    async with get_db_connection() as db:
        # Re-enable sources disabled more than 6 hours ago
        await db.execute("""
            UPDATE sources 
            SET enabled = 1, fail_count = 0, disabled_at = NULL 
            WHERE enabled = 0 AND disabled_at IS NOT NULL AND disabled_at < datetime('now', '-6 hour')
        """)
        await db.commit()
        
        async with db.execute("SELECT id, name, type, url FROM sources WHERE enabled = 1") as cursor:
            sources = await cursor.fetchall()
            
    if tracker:
        await tracker.set_sources_total(len(sources))
            
    all_items = []
    
    for s_id, s_name, s_type, s_url in sources:
        try:
            if s_type == "rss":
                items = await fetch_rss(s_id, s_name, s_url)
            elif s_type == "reddit":
                items = await fetch_reddit(s_id, s_name, s_url)
            elif s_type == "hackernews":
                items = await fetch_hackernews(s_id, s_name, s_url)
            else:
                logger.warning(f"Unknown source type {s_type} for {s_name}")
                continue
                
            all_items.extend(items)
            if tracker:
                await tracker.add_source_ok()
        except Exception as e:
            logger.error(f"Collector error for {s_name}: {e}")
            if tracker:
                await tracker.add_source_failed()
                await tracker.add_error(f"Source {s_name} ({s_type}): {e}")
            
    if tracker:
        await tracker.add_items_raw(len(all_items))
        
    await save_new_items(all_items, tracker)

async def save_new_items(items: list[dict], tracker=None):
    async with get_db_connection() as db:
        # Load recent items for deduplication
        recent_records = []
        async with db.execute("""
            SELECT title, title_hash, url_hash 
            FROM news_items 
            WHERE created_at >= datetime('now', '-14 day')
        """) as cursor:
            recent_records = await cursor.fetchall()
            
        recent_url_hashes = {r[2] for r in recent_records}
        recent_title_hashes = {r[1] for r in recent_records}
        recent_titles_norm = [normalize_title(r[0]) for r in recent_records]
        
        new_count = 0
        for item in items:
            title = item["title"]
            url = item["url"]
            
            if not title or not url:
                continue
                
            title_norm = normalize_title(title)
            title_hash = generate_hash(title_norm)
            url_hash = get_url_hash(url)
            
            # Exact hash match
            if title_hash in recent_title_hashes or url_hash in recent_url_hashes:
                continue
                
            # Fuzzy title match
            is_dup = False
            for r_title_norm in recent_titles_norm:
                if title_similarity(title_norm, r_title_norm) > 0.7:
                    is_dup = True
                    break
                    
            if is_dup:
                continue
                
            # Pre-filter by age
            status = "collected"
            if not item.get("published_at"):
                status = "filtered_out_stale"
                logger.warning(f"Item without valid published_at filtered out: {url}")
            else:
                age_hours = (datetime.utcnow() - item["published_at"]).total_seconds() / 3600.0
                if age_hours > config.max_news_age_hours:
                    status = "filtered_out_stale"
                    logger.info(f"Stale item {url} filtered out (age: {age_hours:.1f}h)")
                    
            # Insert
            await db.execute("""
                INSERT INTO news_items (source_id, url, url_hash, title_hash, title, summary, published_at, image_url, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (item["source_id"], url, url_hash, title_hash, title, item["summary"], item["published_at"], item.get("image_url"), status))
            
            recent_title_hashes.add(title_hash)
            recent_url_hashes.add(url_hash)
            recent_titles_norm.append(title_norm)
            new_count += 1
            
        await db.commit()
        if tracker:
            await tracker.add_items_after_dedup(new_count)
            
        logger.info(f"Collector finished. Inserted {new_count} new items.")
