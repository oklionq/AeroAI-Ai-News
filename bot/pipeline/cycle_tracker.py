import json
from datetime import datetime, timezone
from bot.db import get_db_connection
from bot.utils.logger import logger

class CycleTracker:
    def __init__(self, cycle_id: int):
        self.cycle_id = cycle_id

    @classmethod
    async def start_cycle(cls) -> "CycleTracker":
        async with get_db_connection() as db:
            cursor = await db.execute("""
                INSERT INTO poll_cycles (started_at, status, errors_count, last_errors_json, sources_total, sources_ok, sources_failed, items_raw, items_filtered_stale, items_after_dedup, items_passed_filter, items_sent_moderation, items_auto_published)
                VALUES (?, 'running', 0, '[]', 0, 0, 0, 0, 0, 0, 0, 0, 0)
            """, (datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),))
            cycle_id = cursor.lastrowid
            await db.commit()
        return cls(cycle_id)

    async def _increment_counter(self, column: str, amount: int = 1):
        if amount == 0:
            return
        async with get_db_connection() as db:
            await db.execute(f"UPDATE poll_cycles SET {column} = coalesce({column}, 0) + ? WHERE id = ?", (amount, self.cycle_id))
            await db.commit()

    async def add_source_ok(self):
        await self._increment_counter("sources_ok")

    async def add_source_failed(self):
        await self._increment_counter("sources_failed")

    async def set_sources_total(self, total: int):
        async with get_db_connection() as db:
            await db.execute("UPDATE poll_cycles SET sources_total = ? WHERE id = ?", (total, self.cycle_id))
            await db.commit()

    async def add_items_raw(self, count: int):
        await self._increment_counter("items_raw", count)

    async def add_items_filtered_stale(self, count: int):
        await self._increment_counter("items_filtered_stale", count)

    async def add_items_after_dedup(self, count: int):
        await self._increment_counter("items_after_dedup", count)

    async def add_items_passed_filter(self, count: int):
        await self._increment_counter("items_passed_filter", count)

    async def add_items_sent_moderation(self, count: int):
        await self._increment_counter("items_sent_moderation", count)

    async def add_items_auto_published(self, count: int):
        await self._increment_counter("items_auto_published", count)

    async def add_error(self, message: str):
        await self._increment_counter("errors_count")
        async with get_db_connection() as db:
            async with db.execute("SELECT last_errors_json FROM poll_cycles WHERE id = ?", (self.cycle_id,)) as cursor:
                row = await cursor.fetchone()
                errors = json.loads(row[0]) if row and row[0] else []
            errors.append(message)
            if len(errors) > 5:
                errors = errors[-5:]
            await db.execute("UPDATE poll_cycles SET last_errors_json = ? WHERE id = ?", (json.dumps(errors), self.cycle_id))
            await db.commit()

    async def finish_cycle(self, status: str = "success"):
        async with get_db_connection() as db:
            async with db.execute("SELECT started_at, errors_count, sources_failed FROM poll_cycles WHERE id = ?", (self.cycle_id,)) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return
                started_at_str = row[0]
                errors_count = row[1] or 0
                sources_failed = row[2] or 0
                
            if status == "success" and (errors_count > 0 or sources_failed > 0):
                status = "partial_failure"
            
            now = datetime.now(timezone.utc)
            now_str = now.strftime("%Y-%m-%d %H:%M:%S")
            started_at = datetime.strptime(started_at_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            duration_seconds = int((now - started_at).total_seconds())

            await db.execute("""
                UPDATE poll_cycles 
                SET finished_at = ?, duration_seconds = ?, status = ?
                WHERE id = ?
            """, (now_str, duration_seconds, status, self.cycle_id))
            
            # Update bot_state with next_poll_at
            from bot.config import config
            import datetime as dt
            next_poll_at = now + dt.timedelta(minutes=config.poll_interval_minutes)
            next_poll_at_str = next_poll_at.strftime("%Y-%m-%d %H:%M:%S")
            
            await db.execute("""
                UPDATE bot_state
                SET last_poll_at = ?, next_poll_at = ?
                WHERE id = 1 AND is_paused = 0
            """, (now_str, next_poll_at_str))
            
            await db.execute("""
                UPDATE bot_state
                SET last_poll_at = ?, next_poll_at = NULL
                WHERE id = 1 AND is_paused = 1
            """, (now_str,))
            
            await db.commit()

    @classmethod
    async def cleanup_hung_cycles(cls):
        async with get_db_connection() as db:
            await db.execute("""
                UPDATE poll_cycles 
                SET status = 'partial_failure', 
                    last_errors_json = (
                        SELECT json_insert(coalesce(last_errors_json, '[]'), '$[#]', 'Interrupted by restart')
                        FROM poll_cycles AS p2 WHERE p2.id = poll_cycles.id
                    ),
                    errors_count = coalesce(errors_count, 0) + 1
                WHERE status = 'running'
            """)
            await db.commit()
