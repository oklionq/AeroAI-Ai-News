import asyncio
import json
import cloudscraper
import feedparser
from datetime import datetime
from bot.sources.base import handle_source_error, reset_source_fails
from bot.utils.logger import logger

def _sync_fetch_reddit_rss(url: str) -> str:
    scraper = cloudscraper.create_scraper()
    # Add a custom user-agent to avoid Reddit blocking generic bots
    scraper.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 AeroAI/1.0"})
    resp = scraper.get(url, timeout=15)
    resp.raise_for_status()
    return resp.text

async def fetch_reddit(source_id: int, source_name: str, url: str) -> list[dict]:
    try:
        content = await asyncio.to_thread(_sync_fetch_reddit_rss, url)
        feed = feedparser.parse(content)
        
        if feed.bozo and hasattr(feed, 'bozo_exception'):
            logger.warning(f"Reddit Feed {source_name} has bozo exception: {feed.bozo_exception}")
            
        items = []
        for entry in feed.entries:
            published_parsed = entry.get("published_parsed") or entry.get("updated_parsed")
            if published_parsed:
                published_at = datetime(*published_parsed[:6])
            else:
                published_at = None
                
            image_urls = []
            if getattr(entry, 'media_content', None) and len(entry.media_content) > 0:
                mc_url = entry.media_content[0].get('url')
                if mc_url:
                    image_urls.append(mc_url)
            elif getattr(entry, 'links', None):
                for link in entry.links:
                    if link.get('rel') == 'enclosure' and 'image' in link.get('type', ''):
                        enc_url = link.get('href')
                        if enc_url:
                            image_urls.append(enc_url)
                        break
                
            items.append({
                "title": entry.get("title", ""),
                "summary": entry.get("summary", "") or entry.get("description", ""),
                "url": entry.get("link", ""),
                "published_at": published_at,
                "source_id": source_id,
                "image_urls": json.dumps(image_urls)
            })
            
        await reset_source_fails(source_id)
        return items
    except Exception as e:
        logger.error(f"Failed to fetch Reddit RSS for {source_name}: {e}")
        await handle_source_error(source_id, source_name, str(e))
        return []
