from bot.db import get_db_connection
from bot.services.openai_client import get_filter_decision
from bot.services.budget_guard import check_budget
from bot.utils.logger import logger
import asyncio
async def run_filter_stage(tracker=None):
    if not await check_budget():
        logger.info("Budget exceeded. Skipping filter stage.")
        return
        
    async with get_db_connection() as db:
        async with db.execute("""
            SELECT id, title, summary FROM news_items 
            WHERE status = 'collected' 
            LIMIT 20
        """) as cursor:
            items = await cursor.fetchall()
            
    if not items:
        return
        
    for item_id, title, summary in items:
        if not await check_budget():
            break
            
        logger.info(f"Filtering item {item_id}")
        decision = await get_filter_decision(title, summary, item_id)
        
        async with get_db_connection() as db:
            if decision:
                if decision.is_important and decision.confidence < 0.6:
                    decision.is_important = False
                    decision.reason = f"[Low Confidence: {decision.confidence}] " + decision.reason
                    
                new_status = 'pending_generation' if decision.is_important else 'filtered_out'
                
                if new_status == 'pending_generation':
                    from bot.config import config
                    async with db.execute(f"""
                        SELECT id FROM news_items 
                        WHERE status IN ('published', 'approved', 'pending_review', 'pending_generation', 'processing_generation') 
                        AND subject = ? 
                        AND decision_at >= datetime('now', '-{config.topic_dedup_days} day')
                        AND id != ?
                    """, (decision.subject, item_id)) as dup_cursor:
                        if await dup_cursor.fetchone():
                            new_status = 'duplicate_topic'
                            decision.reason = f"Duplicate of topic {decision.subject}"
                            
                await db.execute("""
                    UPDATE news_items 
                    SET status = ?, filter_category = ?, filter_reason = ?, filter_confidence = ?, subject = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (new_status, decision.category, decision.reason, decision.confidence, decision.subject, item_id))
            else:
                # LLM error
                await db.execute("""
                    UPDATE news_items SET status = 'error', updated_at = CURRENT_TIMESTAMP WHERE id = ?
                """, (item_id,))
                if tracker:
                    await tracker.add_error(f"Filter LLM error for item {item_id}")
            await db.commit()
            
            if decision and new_status == 'pending_generation' and tracker:
                await tracker.add_items_passed_filter(1)
