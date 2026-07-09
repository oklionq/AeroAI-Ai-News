import asyncio
import json
from openai import AsyncOpenAI
from bot.config import config
from bot.models import FilterResult, GeneratedPost
from bot.utils.logger import logger
from bot.db import get_db_connection

client = AsyncOpenAI(api_key=config.openai_api_key)

# PRICING (USD per 1M tokens) - Update manually if needed
PRICING = {
    "gpt-4o-mini": {"input": 0.150, "output": 0.600},
    "gpt-4.1-nano": {"input": 0.150, "output": 0.600}, # Assuming same for nano
    "gpt-4.1-mini": {"input": 0.150, "output": 0.600},
}

async def track_usage(stage: str, model: str, usage, news_item_id: int = None):
    if not usage:
        return
    
    input_tokens = usage.prompt_tokens
    output_tokens = usage.completion_tokens
    
    model_pricing = PRICING.get(model, PRICING["gpt-4o-mini"])
    cost = (input_tokens / 1_000_000 * model_pricing["input"]) + \
           (output_tokens / 1_000_000 * model_pricing["output"])
           
    async with get_db_connection() as db:
        await db.execute("""
            INSERT INTO api_usage (stage, model, input_tokens, output_tokens, cost_usd, news_item_id)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (stage, model, input_tokens, output_tokens, cost, news_item_id))
        
        await db.execute("""
            UPDATE bot_state SET budget_spent_usd = budget_spent_usd + ? WHERE id = 1
        """, (cost,))
        
        await db.commit()

async def get_filter_decision(title: str, summary: str, news_item_id: int) -> FilterResult | None:
    model = config.filter_model
    prompt = f"Title: {title}\n\nSummary: {summary}"
    
    try:
        response = await client.beta.chat.completions.parse(
            model=model,
            messages=[
                {"role": "system", "content": "You are an AI news filter. Determine if the news is important based on these exact criteria: 1. model_release (release of a new model by a major AI lab). 2. feature_update (major new FLAGSHIP update to an existing product, not incremental). 3. benchmark_comparison (comparison with specific numbers). 4. pricing_or_availability (changes to prices or limits). 5. regulatory_or_political (direct impact on model availability or restrictions). 6. competitive_intel (credible info about competitors' progress). Exclude: partnerships, financial/corporate news, safety/influence reports, research programs, infrastructure/datacenters, and minor incremental features. Provide a confidence score (0.0 to 1.0). For Reddit/community rumors without official links, assign confidence < 0.6. You MUST extract the `subject` as the canonical product/model name."},
                {"role": "user", "content": prompt}
            ],
            response_format=FilterResult,
        )
        
        await track_usage("filter", model, response.usage, news_item_id)
        return response.choices[0].message.parsed
    except Exception as e:
        logger.error(f"OpenAI filter error for item {news_item_id}: {e}")
        # One retry
        try:
            response = await client.beta.chat.completions.parse(
                model=model,
                messages=[
                    {"role": "system", "content": "You are an AI news filter. Determine if the news is important based on these exact criteria: 1. model_release (release of a new model by a major AI lab). 2. feature_update (major new FLAGSHIP update to an existing product, not incremental). 3. benchmark_comparison (comparison with specific numbers). 4. pricing_or_availability (changes to prices or limits). 5. regulatory_or_political (direct impact on model availability or restrictions). 6. competitive_intel (credible info about competitors' progress). Exclude: partnerships, financial/corporate news, safety/influence reports, research programs, infrastructure/datacenters, and minor incremental features. Provide a confidence score (0.0 to 1.0). For Reddit/community rumors without official links, assign confidence < 0.6. YOU MUST RETURN STRICT JSON FORMAT. You MUST extract the `subject` as the canonical product/model name."},
                    {"role": "user", "content": prompt}
                ],
                response_format=FilterResult,
            )
            await track_usage("filter_retry", model, response.usage, news_item_id)
            return response.choices[0].message.parsed
        except Exception as retry_e:
            logger.error(f"OpenAI filter retry failed for item {news_item_id}: {retry_e}")
            return None

async def generate_post_text(title: str, summary: str, source_name: str, news_item_id: int, url: str, retry_format: bool = False) -> GeneratedPost | None:
    model = config.generation_model
    prompt = f"Source: {source_name}\nURL: {url}\nTitle: {title}\n\nContent: {summary}"
    
    system_prompt = """
You are a dry, expert AI news writer. Write a Telegram post. Format EXACTLY as below, including all HTML tags:

<b>{эмодзи} {Заголовок в стиле "Вышла X"}</b>

{1-2 вводных абзаца обычным текстом, без разметки}

<blockquote>— {факт 1: обязательно с конкретным числом, названием или датой из источника}
— {факт 2: обязательно с конкретным числом, названием или датой из источника}
— {факт 3: обязательно с конкретным числом, названием или датой из источника}</blockquote>

{Блок цены/доступности, если есть, обычным текстом}

<a href="{URL источника}">Источник</a>

Весь текст поста, включая вводный абзац, пиши строго на русском языке. Не копируй формулировки источника дословно даже частично — перефразируй своими словами.
You must return strictly valid HTML. If a fact lacks concrete details in the source, DO NOT include it. Do not make up facts!
Ensure characters <, >, and & in the text itself are escaped so they don't break HTML formatting. DO NOT escape apostrophes (') or quotes (") as &apos; or &quot; - use the symbols directly.
CRITICAL RULE: NEVER invent, guess, or hallucinate dates, numbers, or names that are not explicitly present in the provided source text. If a date or number is not in the source, simply do not mention it.
CRITICAL RULE FOR REDDIT SOURCES: Reddit posts are community discussions, not confirmed facts. If the Reddit post contains a direct link to an official source (press release, blog, tweet), use facts ONLY from that official link's context if available. If there is no official link, DO NOT publish specific numbers or dates as confirmed facts; only write what is explicitly stated in the text without making up specifics.
"""
    if retry_format:
        system_prompt += "\nYOU MUST RETURN STRICT JSON FORMAT AND INCLUDE <b>, <blockquote>, AND <a href=...> TAGS."
    try:
        response = await client.beta.chat.completions.parse(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ],
            response_format=GeneratedPost,
        )
        await track_usage("generation", model, response.usage, news_item_id)
        return response.choices[0].message.parsed
    except Exception as e:
        logger.error(f"OpenAI generation error for item {news_item_id}: {e}")
        try:
            response = await client.beta.chat.completions.parse(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt + "\nYOU MUST RETURN STRICT JSON FORMAT AND EXACT HTML."},
                    {"role": "user", "content": prompt}
                ],
                response_format=GeneratedPost,
            )
            await track_usage("generation_retry", model, response.usage, news_item_id)
            return response.choices[0].message.parsed
        except Exception as retry_e:
            logger.error(f"OpenAI generation retry failed for item {news_item_id}: {retry_e}")
            return None
