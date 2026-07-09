import asyncio
import os
from bot.db import get_db_connection, DB_PATH

async def reset():
    print(f"DB path: {DB_PATH}")
    async with get_db_connection() as db:
        await db.execute("UPDATE news_items SET status = 'pending_generation' WHERE status = 'processing_generation'")
        
        # Also mark some other item as important to make sure we generate something if previous one wasn't
        await db.execute("UPDATE news_items SET status = 'pending_generation' WHERE id IN (SELECT id FROM news_items WHERE status IN ('collected', 'filtered_out') ORDER BY id DESC LIMIT 1)")
        await db.commit()
    print("Reset done")

if __name__ == "__main__":
    asyncio.run(reset())
