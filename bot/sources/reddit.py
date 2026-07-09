# Reddit RSS parsing is essentially the same as generic RSS, just with different data structure sometimes
# But we can use the same fetch_rss logic for Reddit .rss feeds.
from bot.sources.rss_sources import fetch_rss

async def fetch_reddit(source_id: int, source_name: str, url: str) -> list[dict]:
    # Reddit RSS is standard RSS
    return await fetch_rss(source_id, source_name, url)
