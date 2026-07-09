from bot.db import get_db_connection
from datetime import datetime, timedelta

async def get_stats() -> dict:
    stats = {}
    async with get_db_connection() as db:
        # totals
        async with db.execute("SELECT COUNT(*) FROM news_items") as cur:
            stats['total_collected'] = (await cur.fetchone())[0]
            
        async with db.execute("SELECT COUNT(*) FROM news_items WHERE collected_at >= datetime('now', '-1 day')") as cur:
            stats['collected_24h'] = (await cur.fetchone())[0]
            
        async with db.execute("SELECT COUNT(*) FROM news_items WHERE filter_category IS NOT NULL") as cur:
            stats['passed_filter'] = (await cur.fetchone())[0]
            
        async with db.execute("SELECT COUNT(*) FROM news_items WHERE status = 'filtered_out'") as cur:
            stats['filtered_out'] = (await cur.fetchone())[0]
            
        async with db.execute("SELECT COUNT(*) FROM news_items WHERE status = 'pending_review'") as cur:
            stats['pending_review'] = (await cur.fetchone())[0]
            
        async with db.execute("SELECT COUNT(*) FROM news_items WHERE status = 'published' OR status = 'approved'") as cur:
            stats['approved'] = (await cur.fetchone())[0]
            
        async with db.execute("SELECT COUNT(*) FROM news_items WHERE status = 'rejected'") as cur:
            stats['rejected'] = (await cur.fetchone())[0]
            
        # source stats
        stats['source_stats'] = {}
        async with db.execute("""
            SELECT s.name, COUNT(n.id) 
            FROM sources s LEFT JOIN news_items n ON s.id = n.source_id 
            GROUP BY s.id
        """) as cur:
            async for row in cur:
                stats['source_stats'][row[0]] = row[1]
                
        # budget
        async with db.execute("SELECT SUM(input_tokens + output_tokens), SUM(cost_usd) FROM api_usage") as cur:
            row = await cur.fetchone()
            stats['total_tokens'] = row[0] or 0
            stats['total_cost'] = row[1] or 0.0
            
        async with db.execute("SELECT SUM(cost_usd) FROM api_usage WHERE timestamp >= datetime('now', 'start of month')") as cur:
            row = await cur.fetchone()
            stats['cost_this_month'] = row[0] or 0.0
            
        async with db.execute("SELECT budget_spent_usd, is_paused, pause_reason FROM bot_state WHERE id = 1") as cur:
            row = await cur.fetchone()
            if row:
                stats['budget_spent'] = row[0]
                stats['is_paused'] = bool(row[1])
                stats['pause_reason'] = row[2]
            else:
                stats['budget_spent'] = 0.0
                stats['is_paused'] = False
                stats['pause_reason'] = None
                
    return stats
