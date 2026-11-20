from bot.db import get_db_connection
from bot.utils.logger import logger

async def run_filter_stage(tracker=None):
    """
    Фильтрация отключена: все посты канала-донора считаются полезными
    и сразу направляются на этап генерации и премодерации администратором.
    """
    async with get_db_connection() as db:
        async with db.execute("SELECT id FROM news_items WHERE status = 'collected'") as cur:
            collected_ids = [r[0] for r in await cur.fetchall()]
            
        if collected_ids:
            logger.info(f"Promoting {len(collected_ids)} collected items to pending_generation (filters bypassed).")
            await db.execute("""
                UPDATE news_items 
                SET status = 'pending_generation',
                    filter_category = COALESCE(filter_category, 'other'),
                    filter_reason = 'Filters disabled by admin',
                    filter_confidence = 1.0,
                    decision_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE status = 'collected'
            """)
            await db.commit()
            if tracker:
                await tracker.add_items_passed_filter(len(collected_ids))
