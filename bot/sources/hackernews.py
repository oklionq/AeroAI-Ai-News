import json
from datetime import datetime
from bot.sources.base import fetch_with_retry, handle_source_error, reset_source_fails
from bot.utils.logger import logger

async def fetch_hackernews(source_id: int, source_name: str, url: str) -> list[dict]:
    try:
        response = await fetch_with_retry(url)
        data = response.json()
        
        items = []
        for hit in data.get("hits", []):
            url_link = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
            items.append({
                "title": hit.get("title", ""),
                "summary": hit.get("story_text", "") or "No text provided.",
                "url": url_link,
                "published_at": datetime.utcfromtimestamp(hit.get("created_at_i", 0)),
                "source_id": source_id
            })
            
        await reset_source_fails(source_id)
        return items
    except Exception as e:
        await handle_source_error(source_id, source_name, str(e))
        return []
