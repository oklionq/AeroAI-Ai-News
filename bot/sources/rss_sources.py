import feedparser
from datetime import datetime
import time
from bot.sources.base import fetch_with_retry, FetchError, handle_source_error, reset_source_fails
from bot.utils.logger import logger

async def fetch_rss(source_id: int, source_name: str, url: str) -> list[dict]:
    try:
        response = await fetch_with_retry(url)
        feed = feedparser.parse(response.content)
        
        if feed.bozo and hasattr(feed, 'bozo_exception'):
            logger.warning(f"Feed {source_name} has bozo exception: {feed.bozo_exception}")
            # we can still try to parse entries
            
        items = []
        for entry in feed.entries:
            published_parsed = entry.get("published_parsed") or entry.get("updated_parsed")
            if published_parsed:
                published_at = datetime.fromtimestamp(time.mktime(published_parsed))
            else:
                published_at = None
                
            image_url = None
            if getattr(entry, 'media_content', None) and len(entry.media_content) > 0:
                image_url = entry.media_content[0].get('url')
            elif getattr(entry, 'links', None):
                for link in entry.links:
                    if link.get('rel') == 'enclosure' and 'image' in link.get('type', ''):
                        image_url = link.get('href')
                        break
                
            items.append({
                "title": entry.get("title", ""),
                "summary": entry.get("summary", "") or entry.get("description", ""),
                "url": entry.get("link", ""),
                "published_at": published_at,
                "source_id": source_id,
                "image_url": image_url
            })
            
        await reset_source_fails(source_id)
        return items
    except Exception as e:
        await handle_source_error(source_id, source_name, str(e))
        return []
