import asyncio
import json
import re
from openai import AsyncOpenAI
from bot.config import config
from bot.models import FilterResult, GeneratedPost
from bot.utils.logger import logger
from bot.db import get_db_connection

client = AsyncOpenAI(
    api_key=config.openai_api_key,
    base_url=config.openai_base_url
)

# PRICING (USD per 1M tokens)
PRICING = {
    "deepseek-v4-1-flash-1017-uncensored": {"input": 0.150, "output": 0.600},
    "gpt-4o-mini": {"input": 0.150, "output": 0.600},
    "gpt-4.1-nano": {"input": 0.150, "output": 0.600},
    "gpt-4.1-mini": {"input": 0.150, "output": 0.600},
}

def _clean_json_string(raw: str) -> str:
    """Удаляет маркдаун блоки ```json ... ``` и пробелы."""
    raw = raw.strip()
    match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', raw, re.DOTALL)
    if match:
        return match.group(1).strip()
    # Если просто начинается с { и заканчивается }
    match_braces = re.search(r'(\{.*\})', raw, re.DOTALL)
    if match_braces:
        return match_braces.group(1).strip()
    return raw

async def track_usage(stage: str, model: str, usage, news_item_id: int = None):
    if not usage:
        return
    
    input_tokens = getattr(usage, "prompt_tokens", 0) or 0
    output_tokens = getattr(usage, "completion_tokens", 0) or 0
    
    model_pricing = PRICING.get(model, PRICING["deepseek-v4-1-flash-1017-uncensored"])
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
    
    system_prompt = (
        "You are an AI news filter. Determine if the news is important based on these exact criteria: "
        "1. model_release (release of a new model by a major AI lab). "
        "2. feature_update (major new FLAGSHIP update to an existing product, not incremental). "
        "3. benchmark_comparison (comparison with specific numbers). "
        "4. pricing_or_availability (changes to prices or limits). "
        "5. regulatory_or_political (direct impact on model availability or restrictions). "
        "6. competitive_intel (credible info about competitors' progress). "
        "Exclude: advertisements, sponsorships, promotional messages, partnerships, financial/corporate rumors, "
        "and minor incremental features. Provide a confidence score (0.0 to 1.0). "
        "You MUST extract the `subject` as the canonical product/model name.\n"
        "Output ONLY a valid JSON object with the following fields:\n"
        "{\n"
        '  "is_important": boolean,\n'
        '  "category": "model_release" | "feature_update" | "benchmark_comparison" | "pricing_or_availability" | "regulatory_or_political" | "competitive_intel" | "other",\n'
        '  "subject": "string",\n'
        '  "reason": "1 short sentence explaining the decision",\n'
        '  "confidence": float between 0.0 and 1.0\n'
        "}"
    )

    for attempt in range(2):
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt}
                ],
                response_format={"type": "json_object"},
                temperature=0.1
            )
            
            raw_content = response.choices[0].message.content or ""
            cleaned = _clean_json_string(raw_content)
            data = json.loads(cleaned)
            parsed = FilterResult.model_validate(data)
            
            await track_usage("filter" if attempt == 0 else "filter_retry", model, response.usage, news_item_id)
            return parsed
        except Exception as e:
            logger.warning(f"Filter attempt {attempt + 1} failed for item {news_item_id}: {e}")
            if attempt == 1:
                logger.error(f"Filter all attempts failed for item {news_item_id}: {e}")
                return None
            await asyncio.sleep(1)

    return None

async def _check_and_fix_grammar(post_html: str) -> str:
    match = re.search(r'<b>(.*?)</b>', post_html)
    if not match:
        return post_html
        
    full_title = match.group(1)
    grammar_prompt = "Проверь этот короткий заголовок на грамматическое согласование рода и падежа. Если ошибок нет — верни его без изменений. Если есть — верни исправленный вариант. Внимание: в начале заголовка может быть эмодзи, сохрани его. Не добавляй пояснений, только заголовок."
    try:
        grammar_resp = await client.chat.completions.create(
            model=config.filter_model,
            messages=[
                {"role": "system", "content": grammar_prompt},
                {"role": "user", "content": full_title}
            ],
            max_tokens=100,
            temperature=0.1
        )
        fixed_title = grammar_resp.choices[0].message.content.strip() if grammar_resp.choices else ""
        if fixed_title:
            clean_fixed = re.sub(r'^(исправленный вариант|исправленный заголовок|исправление|вариант):\s*', '', fixed_title, flags=re.IGNORECASE).strip(' "\'')
            if clean_fixed and clean_fixed != full_title:
                logger.info(f"Грамматическое исправление: '{full_title}' -> '{clean_fixed}'")
                return post_html.replace(f"<b>{full_title}</b>", f"<b>{clean_fixed}</b>")
    except Exception as e:
        logger.error(f"Grammar check error: {e}")
        
    return post_html

async def generate_post_text(title: str, summary: str, source_name: str, news_item_id: int, url: str, retry_format: bool = False, category: str = "other") -> GeneratedPost | None:
    model = config.generation_model
    prompt = f"Source: {source_name}\nURL: {url}\nTitle: {title}\n\nContent: {summary}"
    
    gender_rules = ""
    if category == "model_release":
        gender_rules = "\nОПОРНОЕ СЛОВО для заголовка: 'модель' (женский род). ВСЕ элементы заголовка (глагол, прилагательные) должны быть согласованы с женским родом, даже если само слово 'модель' не произносится. Пример правильно: 'Вышла первая открытая модель от X'. Пример НЕПРАВИЛЬНО: 'Вышел первый открытый модель от X'."
    elif category == "feature_update":
        gender_rules = "\nОПОРНОЕ СЛОВО для заголовка: 'функция' (ж.р.) или 'обновление' (ср.р.). Выбери одно и строго согласуй весь заголовок с его родом."
    elif category in ["benchmark_comparison", "competitive_intel"]:
        gender_rules = "\nОПОРНОЕ СЛОВО для заголовка: 'тест' (м.р.) или 'сравнение' (ср.р.). Строго согласуй весь заголовок с его родом."
    elif category == "pricing_or_availability":
        gender_rules = "\nОПОРНОЕ СЛОВО для заголовка: 'цена' (ж.р.) или 'тариф' (м.р.). Строго согласуй весь заголовок с его родом."
    elif category == "regulatory_or_political":
        gender_rules = "\nОПОРНОЕ СЛОВО для заголовка: 'закон' (м.р.) или 'регулирование' (ср.р.). Строго согласуй весь заголовок с его родом."

    length_rule = (
        "\nКРИТИЧЕСКОЕ ТРЕБОВАНИЕ К ДЛИНЕ ПОСТА: Суммарная длина текста со всеми HTML-тегами "
        "ОБЯЗАНА быть не более 800-850 символов! Текст будет прикреплён как подпись (caption) к фото/альбому в Telegram. "
        "Пиши лаконично, плотно и сухо: ровно 1 короткий вводный абзац (2-3 предложения) и блок <blockquote> с 2-3 фактами. "
        "НЕ добавляй ссылки на источник или внешние каналы в конце поста! Никакой воды и растянутых вступлений!"
    )

    system_prompt = f"""
You are a dry, expert AI news writer. Write a Telegram post. Format EXACTLY as below, including all HTML tags:

<b>{{эмодзи}} {{Заголовок в стиле "Вышла X"}}</b>

{{1 короткий вводный абзац обычным текстом, без разметки}}

<blockquote>— {{факт 1: обязательно с конкретным числом, названием или датой из источника}}
— {{факт 2: обязательно с конкретным числом, названием или датой из источника}}
— {{факт 3: обязательно с конкретным числом, названием или датой из источника}}</blockquote>

{{Короткий блок цены/доступности, если есть, обычным текстом}}

Весь текст поста, включая вводный абзац, пиши строго на русском языке. Не копируй формулировки источника дословно даже частично — перефразируй своими словами.
ВАЖНО: НЕ добавляй в конце ссылку на источник («Источник» или URL). Ссылок быть не должно!
You must return strictly valid HTML inside a JSON object with key "post_html".
Ensure characters <, >, and & in the text itself are escaped so they don't break HTML formatting. DO NOT escape apostrophes (') or quotes (") as &apos; or &quot; - use the symbols directly.
CRITICAL RULE: NEVER invent, guess, or hallucinate dates, numbers, or names that are not explicitly present in the provided source text. If a date or number is not in the source, simply do not mention it.
{gender_rules}
{length_rule}

Output JSON format:
{{"post_html": "<b>...</b>\\n\\n...\\n\\n<blockquote>...</blockquote>"}}
"""
    if retry_format:
        system_prompt += "\nYOU MUST RETURN STRICT JSON FORMAT WITH KEY 'post_html' AND INCLUDE <b> AND <blockquote> TAGS. DO NOT INCLUDE SOURCE LINKS."

    for attempt in range(2):
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt}
                ],
                response_format={"type": "json_object"},
                temperature=0.2
            )
            raw_content = response.choices[0].message.content or ""
            cleaned = _clean_json_string(raw_content)
            data = json.loads(cleaned)
            
            # Handle if model returned {"post_html": "..."} or {"text": "..."}
            if "post_html" not in data and "text" in data:
                data["post_html"] = data["text"]
                
            parsed_post = GeneratedPost.model_validate(data)
            parsed_post.post_html = await _check_and_fix_grammar(parsed_post.post_html)
            await track_usage("generation" if attempt == 0 else "generation_retry", model, response.usage, news_item_id)
            return parsed_post
        except Exception as e:
            logger.warning(f"Generation attempt {attempt + 1} failed for item {news_item_id}: {e}")
            if attempt == 1:
                logger.error(f"Generation all attempts failed for item {news_item_id}: {e}")
                return None
            await asyncio.sleep(1)

    return None
