import asyncio
from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from bot.config import config
from bot.db import init_db
from bot.telegram.handlers import dp
import bot.telegram.admin_commands
from bot.pipeline.collector import run_collector
from bot.pipeline.filter_stage import run_filter_stage
from bot.pipeline.generator_stage import run_generator_stage
from bot.utils.logger import logger
from bot.pipeline.cycle_tracker import CycleTracker

async def tick(bot: Bot):
    try:
        tracker = await CycleTracker.start_cycle()
        try:
            await run_collector(tracker)
            await run_filter_stage(tracker)
            await run_generator_stage(bot, tracker)
            await tracker.finish_cycle(status="success")
        except Exception as e:
            logger.error(f"Error in pipeline stages: {e}", exc_info=True)
            await tracker.add_error(f"Pipeline error: {str(e)}")
            await tracker.finish_cycle(status="partial_failure")
    except Exception as e:
        logger.error(f"Error starting pipeline tick: {e}", exc_info=True)
        try:
            if 'tracker' in locals() and tracker:
                await tracker.finish_cycle(status="partial_failure")
        except:
            pass

_last_watchdog_warning_time = None

async def watchdog_task(bot: Bot):
    from bot.db import get_db_connection
    from datetime import datetime, timezone
    import datetime as dt
    global _last_watchdog_warning_time
    
    while True:
        await asyncio.sleep(5 * 60)
        try:
            async with get_db_connection() as db:
                async with db.execute("SELECT last_poll_at, next_poll_at, is_paused FROM bot_state WHERE id = 1") as cur:
                    row = await cur.fetchone()
                if not row: continue
                last_poll, next_poll, is_paused = row
                if is_paused or not next_poll: continue
                
                next_poll_dt = datetime.strptime(next_poll, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                threshold = next_poll_dt + dt.timedelta(minutes=config.poll_interval_minutes * 2)
                
                if datetime.now(timezone.utc) > threshold:
                    if _last_watchdog_warning_time != last_poll:
                        _last_watchdog_warning_time = last_poll
                        msg = f"⚠️ Цикл парсинга не выполнялся дольше ожидаемого (последний — {last_poll}). Возможно, планировщик остановился."
                        logger.critical(msg)
                        try:
                            await bot.send_message(config.admin_chat_id, msg)
                        except Exception as e:
                            logger.error(f"Watchdog failed to send message: {e}")
                            
                        logger.info("Watchdog is attempting to restart the cycle...")
                        asyncio.create_task(tick(bot))
        except Exception as e:
            logger.error(f"Watchdog error: {e}")

async def reset_monthly_budget():
    from bot.db import get_db_connection
    async with get_db_connection() as db:
        await db.execute("UPDATE bot_state SET budget_spent_usd = 0.0, is_paused = 0, pause_reason = NULL WHERE id = 1 AND pause_reason = 'budget_exceeded'")
        await db.commit()
    logger.info("Monthly budget reset applied.")

async def main():
    import os
    if os.getenv("RAILWAY_ENVIRONMENT") and not config.database_url:
        logger.warning("=" * 60)
        logger.warning("CRITICAL WARNING: Running in Railway without DATABASE_URL set!")
        logger.warning("Your database (data/bot.db) will be LOST on every redeploy.")
        logger.warning("Please create a Volume in Railway, mount it to /data, and")
        logger.warning("set the environment variable DATABASE_URL=/data/bot.db")
        logger.warning("=" * 60)

    logger.info("Initializing DB...")
    await init_db()
    
    logger.info("Cleaning up hung cycles...")
    await CycleTracker.cleanup_hung_cycles()
    
    bot = Bot(token=config.telegram_bot_token)
    
    scheduler = AsyncIOScheduler()
    
    from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_MISSED
    def scheduler_listener(event):
        if event.code == EVENT_JOB_ERROR:
            logger.error(f"Scheduler job error: {event.exception}", exc_info=True)
        elif event.code == EVENT_JOB_MISSED:
            logger.error(f"Scheduler job missed execution time")
    scheduler.add_listener(scheduler_listener, EVENT_JOB_ERROR | EVENT_JOB_MISSED)
    
    scheduler.add_job(tick, 'interval', minutes=config.poll_interval_minutes, args=[bot], misfire_grace_time=3600)
    scheduler.add_job(reset_monthly_budget, 'cron', day=1, hour=0, minute=0)
    
    # Run immediately once
    scheduler.add_job(tick, args=[bot])
    
    scheduler.start()
    asyncio.create_task(watchdog_task(bot))
    
    logger.info("Starting polling...")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
