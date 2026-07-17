from typing import Optional, Any
from datetime import datetime
from pydantic import BaseModel, Field

class FilterResult(BaseModel):
    is_important: bool
    category: str = Field(description="model_release|feature_update|benchmark_comparison|pricing_or_availability|regulatory_or_political|competitive_intel|other")
    subject: str = Field(description="Canonical name of the product/model/subject (e.g. 'Grok 4.5', 'GPT-5.6')")
    reason: str = Field(description="1 short sentence explaining the decision")
    confidence: float = Field(description="Confidence score between 0.0 and 1.0")

class GeneratedPost(BaseModel):
    post_html: str = Field(description="HTML formatted text of the post for Telegram")

class NewsItem(BaseModel):
    id: Optional[int] = None
    source_id: int
    url: str
    url_hash: str
    title_hash: str
    title: str
    summary: str
    published_at: Optional[datetime] = None
    collected_at: Optional[datetime] = None
    status: str = "collected" # collected | filtered_out | pending_review | approved | published | rejected | skipped_no_image | error | duplicate_topic
    filter_category: Optional[str] = None
    subject: Optional[str] = None
    filter_reason: Optional[str] = None
    image_urls: list[str] = []
    image_file_ids: list[str] = []
    post_text_json: Optional[str] = None
    telegram_message_id: Optional[int] = None
    telegram_album_message_ids: list[int] = []
    telegram_control_message_id: Optional[int] = None
    thread_id: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

class Stats(BaseModel):
    total_collected: int
    collected_24h: int
    passed_filter: int
    filtered_out: int
    pending_review: int
    approved: int
    rejected: int
    source_stats: dict[str, int]
    total_tokens: int
    total_cost: float
    cost_this_month: float
    budget_remaining: float
