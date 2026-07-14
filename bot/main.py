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
            logger.error(f"Error in pipeline stages: {e}")
            await tracker.add_error(f"Pipeline error: {str(e)}")
            await tracker.finish_cycle(status="partial_failure")
    except Exception as e:
        logger.error(f"Error starting pipeline tick: {e}")

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
    scheduler.add_job(tick, 'interval', minutes=config.poll_interval_minutes, args=[bot], next_run_time=None)
    scheduler.add_job(reset_monthly_budget, 'cron', day=1, hour=0, minute=0)
    
    # Run immediately once
    scheduler.add_job(tick, args=[bot])
    
    scheduler.start()
    
    logger.info("Starting polling...")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
