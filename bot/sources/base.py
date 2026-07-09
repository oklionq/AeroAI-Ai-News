import httpx
import asyncio
from bot.utils.logger import logger
from bot.db import get_db_connection, log_error

class FetchError(Exception):
    pass

async def fetch_with_retry(url: str, retries: int = 3, backoff: float = 1.0) -> httpx.Response:
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"}
    async with httpx.AsyncClient(timeout=15.0, headers=headers) as client:
        for attempt in range(retries):
            try:
                response = await client.get(url, follow_redirects=True)
                response.raise_for_status()
                return response
            except httpx.HTTPError as e:
                logger.warning(f"Fetch attempt {attempt + 1} failed for {url}: {e}")
                if attempt == retries - 1:
                    raise FetchError(f"Failed to fetch {url} after {retries} attempts: {e}")
                await asyncio.sleep(backoff * (2 ** attempt))

async def handle_source_error(source_id: int, source_name: str, error_msg: str):
    await log_error(f"source_{source_name}", error_msg)
    async with get_db_connection() as db:
        await db.execute("""
            UPDATE sources 
            SET fail_count = fail_count + 1, last_error = ? 
            WHERE id = ?
        """, (error_msg, source_id))
        
        # Check if we should disable
        async with db.execute("SELECT fail_count FROM sources WHERE id = ?", (source_id,)) as cur:
            row = await cur.fetchone()
            if row and row[0] >= 5:
                await db.execute("UPDATE sources SET enabled = 0, disabled_at = CURRENT_TIMESTAMP WHERE id = ?", (source_id,))
                logger.error(f"Source {source_name} disabled after 5 consecutive failures.")
        
        await db.commit()

async def reset_source_fails(source_id: int):
    async with get_db_connection() as db:
        await db.execute("""
            UPDATE sources 
            SET fail_count = 0, last_success_at = CURRENT_TIMESTAMP 
            WHERE id = ?
        """, (source_id,))
        await db.commit()
