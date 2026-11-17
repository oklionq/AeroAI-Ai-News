import os
import json
import re
import asyncio
from datetime import datetime, timezone
import httpx
from bs4 import BeautifulSoup
from telethon import TelegramClient
from telethon.errors import FloodWaitError
from bot.config import config
from bot.utils.logger import logger

BASE_DATA_DIR = os.path.dirname(config.database_url) if config.database_url else "data"
TEMP_MEDIA_DIR = os.path.join(BASE_DATA_DIR, "temp_media")

AD_MARKERS = [
    r"\berid[:\s]",
    r"#реклама\b",
    r"#партнерский\b",
    r"#партнёрский\b",
    r"#ad\b",
    r"#promo\b",
    r"реклама\s+на\s+канале",
]

def is_ad_post(text: str) -> bool:
    if not text:
        return False
    lower = text.lower()
    for pattern in AD_MARKERS:
        if re.search(pattern, lower):
            return True
    return False

def extract_title_and_summary(text: str) -> tuple[str, str]:
    """Извлекает первую содержательную строку как title, остальное как summary."""
    clean_text = text.strip() if text else ""
    if not clean_text:
        return ("Без текста", "")
    lines = [line.strip() for line in clean_text.split("\n") if line.strip()]
    title = lines[0] if lines else "Новость из Telegram"
    if len(title) > 200:
        title = title[:197] + "..."
    return (title, clean_text)

async def _download_file(url: str, dest_path: str) -> bool:
    """Скачивает медиафайл (фото или видео) по URL в локальный файл."""
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(url, follow_redirects=True, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            })
            if resp.status_code == 200 and resp.content:
                with open(dest_path, "wb") as f:
                    f.write(resp.content)
                return True
    except Exception as e:
        logger.warning(f"Failed to download media {url}: {e}")
    return False

async def fetch_via_web(channel_username: str, source_id: int) -> list[dict]:
    """
    Парсит открытый веб-интерфейс канала https://t.me/s/{channel}.
    Извлекает как фотографии, так и полноразмерные MP4-видео.
    """
    url = f"https://t.me/s/{channel_username}"
    logger.info(f"Fetching posts from public web interface: {url}")
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    try:
        async with httpx.AsyncClient(timeout=25.0, headers=headers) as client:
            resp = await client.get(url, follow_redirects=True)
            if resp.status_code != 200:
                logger.error(f"Failed to fetch {url}, status code: {resp.status_code}")
                return []
            html = resp.text
    except Exception as e:
        logger.error(f"HTTP error fetching {url}: {e}")
        return []

    soup = BeautifulSoup(html, "html.parser")
    widgets = soup.find_all("div", class_="tgme_widget_message")
    if not widgets:
        logger.warning(f"No message widgets found on {url}")
        return []

    items = []
    for w in widgets:
        data_post = w.get("data-post") or ""
        if "/" not in data_post:
            continue
            
        try:
            msg_id = int(data_post.split("/")[-1])
        except ValueError:
            continue

        # Текст поста
        text_div = w.find("div", class_="tgme_widget_message_text")
        text = text_div.get_text("\n").strip() if text_div else ""

        # Пропускаем рекламу
        if is_ad_post(text):
            logger.info(f"Skipping ad post #{msg_id} from {channel_username}")
            continue

        # Дата публикации
        time_tag = w.find("time")
        dt_str = time_tag.get("datetime") if time_tag else None
        if dt_str:
            try:
                published_at = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
            except Exception:
                published_at = datetime.now(timezone.utc)
        else:
            published_at = datetime.now(timezone.utc)

        saved_media = []

        # 1. Проверяем наличие ВИДЕО (<video src="...">)
        video_tag = w.find("video", class_="tgme_widget_message_video")
        if not video_tag:
            video_tag = w.find("video")
            
        if video_tag and video_tag.get("src"):
            video_url = video_tag.get("src")
            dest_filename = f"tg_{channel_username}_{msg_id}_video.mp4"
            dest_path = os.path.join(TEMP_MEDIA_DIR, dest_filename)
            
            if not os.path.exists(dest_path):
                logger.info(f"Found video in post #{msg_id}, downloading: {video_url[:80]}...")
                success = await _download_file(video_url, dest_path)
                if success:
                    saved_media.append(dest_path)
            else:
                saved_media.append(dest_path)

        # 2. Если видео нет (или в дополнение к нему), ищем ФОТОГРАФИИ
        # Исключаем превью видео (i.tgme_widget_message_video_thumb)
        photo_elements = w.find_all("a", class_="tgme_widget_message_photo_wrap")
        img_idx = 0
        for pe in photo_elements:
            # Пропускаем, если этот photo_wrap является оберткой видео
            if pe.find("i", class_="tgme_widget_message_video_thumb"):
                continue

            style = pe.get("style", "")
            match = re.search(r"background-image:\s*url\(['\"]?([^'\")]+)['\"]?\)", style)
            if match and len(saved_media) < 4:
                img_url = match.group(1)
                img_idx += 1
                dest_filename = f"tg_{channel_username}_{msg_id}_{img_idx}.jpg"
                dest_path = os.path.join(TEMP_MEDIA_DIR, dest_filename)
                
                if not os.path.exists(dest_path):
                    success = await _download_file(img_url, dest_path)
                    if success:
                        saved_media.append(dest_path)
                else:
                    saved_media.append(dest_path)

        if not text and not saved_media:
            continue

        title, summary = extract_title_and_summary(text)
        post_url = f"https://t.me/{channel_username}/{msg_id}"

        items.append({
            "source_id": source_id,
            "source_msg_id": msg_id,
            "title": title,
            "summary": summary if summary else title,
            "url": post_url,
            "published_at": published_at,
            "image_urls": json.dumps(saved_media)
        })

    logger.info(f"Web parser collected {len(items)} posts from {channel_username}")
    return items

async def fetch_via_telethon(channel_username: str, source_id: int, limit: int = 30) -> list[dict]:
    """
    Парсит сообщения через Telethon Userbot.
    Скачивает оригинальные полноразмерные фото и оригинальные MP4-видео напрямую из MTProto.
    """
    client = TelegramClient(
        config.telethon_session_name,
        config.telethon_api_id,
        config.telethon_api_hash
    )
    items = []
    try:
        await client.connect()
        if not await client.is_user_authorized():
            logger.info("Telethon session not authorized.")
            await client.disconnect()
            return []

        entity = await client.get_entity(channel_username)
        messages = await client.get_messages(entity, limit=limit)
        if not messages:
            return []

        grouped_posts: dict[int | str, dict] = {}
        for msg in sorted(messages, key=lambda m: m.id):
            if msg.action:
                continue
            group_key = msg.grouped_id if msg.grouped_id else f"single_{msg.id}"
            if group_key not in grouped_posts:
                grouped_posts[group_key] = {
                    "root_id": msg.id,
                    "date": msg.date.replace(tzinfo=timezone.utc) if msg.date else datetime.now(timezone.utc),
                    "text": "",
                    "messages": [],
                }
            grouped_posts[group_key]["messages"].append(msg)
            if msg.text and not grouped_posts[group_key]["text"]:
                grouped_posts[group_key]["text"] = msg.text

        for group_key, post_data in grouped_posts.items():
            text = post_data["text"]
            root_id = post_data["root_id"]
            post_date = post_data["date"]

            has_media = any(m.media for m in post_data["messages"])
            if not text and not has_media:
                continue

            if is_ad_post(text):
                continue

            saved_media = []
            media_idx = 0
            for m in post_data["messages"]:
                if len(saved_media) >= 4:
                    break

                # 1. Если видео — скачиваем оригинальный MP4
                if m.video:
                    media_idx += 1
                    target_filename = f"tg_{channel_username}_{root_id}_{media_idx}.mp4"
                    target_path = os.path.join(TEMP_MEDIA_DIR, target_filename)
                    try:
                        if not os.path.exists(target_path):
                            logger.info(f"Downloading original video from Telethon for post {root_id}...")
                            downloaded = await client.download_media(m.video, file=target_path)
                            if downloaded and os.path.exists(downloaded):
                                saved_media.append(downloaded)
                        else:
                            saved_media.append(target_path)
                    except Exception as e:
                        logger.warning(f"Error downloading video for message {m.id}: {e}")

                # 2. Если фото — скачиваем полноразмерный оригинал
                elif m.photo:
                    media_idx += 1
                    target_filename = f"tg_{channel_username}_{root_id}_{media_idx}.jpg"
                    target_path = os.path.join(TEMP_MEDIA_DIR, target_filename)
                    try:
                        if not os.path.exists(target_path):
                            logger.info(f"Downloading original full-res photo from Telethon for post {root_id}...")
                            downloaded = await client.download_media(m.photo, file=target_path)
                            if downloaded and os.path.exists(downloaded):
                                saved_media.append(downloaded)
                        else:
                            saved_media.append(target_path)
                    except Exception as e:
                        logger.warning(f"Error downloading photo for message {m.id}: {e}")

            title, summary = extract_title_and_summary(text)
            post_url = f"https://t.me/{channel_username}/{root_id}"

            items.append({
                "source_id": source_id,
                "source_msg_id": root_id,
                "title": title,
                "summary": summary if summary else title,
                "url": post_url,
                "published_at": post_date,
                "image_urls": json.dumps(saved_media)
            })

    except FloodWaitError as fwe:
        logger.warning(f"Telethon FloodWait: {fwe.seconds}s")
    except Exception as e:
        logger.error(f"Telethon fetch error: {e}")
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass

    return items

async def fetch_telegram_channel(source_id: int = 1, source_name: str = "ThePerceptronChannel", limit: int = 30) -> list[dict]:
    """
    Основная точка входа для сбора постов из канала-донора.
    Если доступна сессия Telethon Userbot — использует её для получения 100% оригиналов фото и видео.
    Иначе использует веб-интерфейс t.me/s/{channel} со скачиванием MP4-видео и фотографий.
    """
    os.makedirs(TEMP_MEDIA_DIR, exist_ok=True)
    channel_username = config.donor_channel.lstrip("@")
    session_file = f"{config.telethon_session_name}.session"

    # Если есть файл сессии — пробуем через Telethon Userbot для максимального качества
    if os.path.exists(session_file):
        try:
            items = await fetch_via_telethon(channel_username, source_id, limit=limit)
            if items:
                logger.info(f"Telethon fetched {len(items)} posts in original quality.")
                return items
        except Exception as e:
            logger.warning(f"Telethon fetch failed, falling back to web: {e}")

    # Fallback на открытый веб-интерфейс (с поддержкой MP4 видео)
    return await fetch_via_web(channel_username, source_id)
