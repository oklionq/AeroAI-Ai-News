from bot.db import get_db_connection, log_error
from bot.config import config
from bot.utils.logger import logger

async def check_budget() -> bool:
    """Returns True if budget is OK, False if exceeded."""
    async with get_db_connection() as db:
        async with db.execute("SELECT budget_spent_usd, is_paused FROM bot_state WHERE id = 1") as cursor:
            row = await cursor.fetchone()
            if not row:
                return True
            
            budget_spent, is_paused = row
            
            if is_paused:
                return False
                
            if budget_spent >= config.max_budget_usd:
                await db.execute("UPDATE bot_state SET is_paused = 1, pause_reason = 'budget_exceeded' WHERE id = 1")
                await db.commit()
                await log_error("budget_guard", f"Budget exceeded: {budget_spent:.2f} >= {config.max_budget_usd}. Pausing bot.")
                return False
                
            # Could add logic for warning at 90%
            return True
