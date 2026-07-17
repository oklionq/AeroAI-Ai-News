import asyncio
import io
import html
import re
from urllib.parse import urlparse

import cloudscraper
import httpx
from bs4 import BeautifulSoup
from PIL import Image

from bot.config import config
from bot.utils.logger import logger

MAX_IMAGES = 4
_REDDIT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AeroAI-NewsBot/2.0 (by /u/AeroAI)"
_GENERIC_UA = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def _sync_fetch_html(url: str) -> tuple[int, bytes]:
    scraper = cloudscraper.create_scraper()
    resp = scraper.get(url, timeout=15)
    return resp.status_code, resp.content


async def _download_image_bytes(url: str) -> bytes | None:
    """Download the image content (up to 20 MB) for validation."""
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(url, follow_redirects=True, headers={"User-Agent": _GENERIC_UA})
            if resp.status_code != 200:
                return None
            ct = resp.headers.get("Content-Type", "")
            if not ct.startswith("image/") and not url.lower().split("?")[0].endswith(
                (".png", ".jpg", ".jpeg", ".webp", ".gif")
            ):
                return None
            return resp.content
    except Exception as e:
        logger.warning(f"Failed to download image {url}: {e}")
        return None


async def _validate_image(url: str) -> bool:
    """Check Content-Type + download and verify width >= MIN_IMAGE_WIDTH via Pillow."""
    data = await _download_image_bytes(url)
    if not data:
        return False
    try:
        img = Image.open(io.BytesIO(data))
        w, _ = img.size
        if w < config.min_image_width:
            logger.info(f"Image too small ({w}px wide, min {config.min_image_width}px): {url}")
            return False
        return True
    except Exception as e:
        logger.warning(f"Pillow could not open image {url}: {e}")
        return False


async def _filter_valid_images(candidates: list[str]) -> list[str]:
    """Validate a list of candidate URLs and return up to MAX_IMAGES valid ones."""
    valid: list[str] = []
    seen: set[str] = set()
    for url in candidates:
        # Deduplicate by normalised URL (strip query for comparison)
        norm = url.split("?")[0]
        if norm in seen:
            continue
        seen.add(norm)

        if await _validate_image(url):
            valid.append(url)
            if len(valid) >= MAX_IMAGES:
                break
    return valid


# ---------------------------------------------------------------------------
# Reddit image extraction (JSON API + fallbacks)
# ---------------------------------------------------------------------------

async def _fetch_reddit_json(post_url: str) -> dict | None:
    """Fetch structured post data via Reddit's public JSON endpoint."""
    try:
        # Normalise URL: strip trailing slash, append .json
        clean = post_url.rstrip("/")
        if not clean.endswith(".json"):
            clean += ".json"

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                clean,
                follow_redirects=True,
                headers={"User-Agent": _REDDIT_UA},
            )
            if resp.status_code != 200:
                logger.warning(f"Reddit JSON returned {resp.status_code} for {clean}")
                return None
            data = resp.json()
            # Reddit returns a list of Listing objects; first element has the post
            if isinstance(data, list) and len(data) > 0:
                children = data[0].get("data", {}).get("children", [])
                if children:
                    return children[0].get("data", {})
    except Exception as e:
        logger.warning(f"Reddit JSON fetch failed for {post_url}: {e}")
    return None


def _unescape(url: str) -> str:
    return html.unescape(url)


async def fetch_reddit_images(html_content: str, post_url: str) -> list[str]:
    """
    Extract up to MAX_IMAGES from a Reddit post.
    Strategy:
      1. Try Reddit JSON API for full-res images / gallery
      2. Fallback: parse existing HTML for preview URLs, try to upgrade them
    """
    candidates: list[str] = []

    # --- Strategy 1: Reddit JSON API ---
    post_data = await _fetch_reddit_json(post_url)
    if post_data:
        is_gallery = post_data.get("is_gallery", False)

        if is_gallery:
            # Gallery post
            gallery_items = post_data.get("gallery_data", {}).get("items", [])
            media_metadata = post_data.get("media_metadata", {})
            for g_item in gallery_items:
                media_id = g_item.get("media_id")
                if not media_id or media_id not in media_metadata:
                    continue
                meta = media_metadata[media_id]
                # Prefer 's' (source) -> 'u' (url)
                src = meta.get("s", {})
                img_url = src.get("u") or src.get("gif")
                if img_url:
                    candidates.append(_unescape(img_url))
                if len(candidates) >= MAX_IMAGES:
                    break
        else:
            # Single-image post: try preview -> source
            preview = post_data.get("preview", {})
            images = preview.get("images", [])
            if images:
                source_url = images[0].get("source", {}).get("url")
                if source_url:
                    candidates.append(_unescape(source_url))

            # Also check direct URL (i.redd.it links)
            direct_url = post_data.get("url", "")
            if direct_url and ("i.redd.it" in direct_url or "i.imgur.com" in direct_url):
                if direct_url not in candidates:
                    candidates.insert(0, direct_url)  # prefer direct link

    # --- Strategy 2: Fallback from HTML content ---
    if not candidates:
        try:
            soup = BeautifulSoup(html_content, "html.parser")
            for img in soup.find_all("img"):
                src = img.get("src", "")
                if "preview.redd.it" in src or "i.redd.it" in src or "external-preview.redd.it" in src:
                    unescaped = _unescape(src)
                    # Try to upgrade preview.redd.it -> i.redd.it (full res)
                    upgraded = _try_upgrade_reddit_preview(unescaped)
                    candidates.append(upgraded)
        except Exception as e:
            logger.error(f"Error parsing Reddit HTML for images: {e}")

    if not candidates:
        logger.info(f"No Reddit image candidates found for {post_url}")
        return []

    return await _filter_valid_images(candidates)


def _try_upgrade_reddit_preview(url: str) -> str:
    """
    Heuristic: replace preview.redd.it with i.redd.it and strip query params.
    This often gives the original full-res image.
    """
    if "preview.redd.it" in url:
        # Extract the image ID from preview URL path
        parsed = urlparse(url)
        path = parsed.path  # e.g. /abcdef.jpg
        upgraded = f"https://i.redd.it{path}"
        logger.info(f"Upgraded Reddit preview: {url} -> {upgraded}")
        return upgraded
    return url


# ---------------------------------------------------------------------------
# OG / generic image extraction (multi-image)
# ---------------------------------------------------------------------------

async def fetch_og_images(url: str) -> list[str]:
    """
    Extract up to MAX_IMAGES from an article page.
    Sources: all og:image tags, twitter:image, media:content / enclosure from RSS.
    """
    candidates: list[str] = []
    try:
        status_code, content = await asyncio.to_thread(_sync_fetch_html, url)
        if status_code != 200:
            logger.warning(f"Failed to fetch {url}, status code: {status_code}")
            return []

        soup = BeautifulSoup(content, "html.parser")

        # Collect ALL og:image tags (some sites declare multiple)
        for og in soup.find_all("meta", property="og:image"):
            img_url = og.get("content")
            if img_url:
                candidates.append(img_url)

        # twitter:image as fallback
        if not candidates:
            tw = soup.find("meta", attrs={"name": "twitter:image"})
            if tw and tw.get("content"):
                candidates.append(tw["content"])

    except Exception as e:
        logger.error(f"Failed to fetch images for {url}: {e}")

    if not candidates:
        logger.info(f"No og:image or twitter:image found for {url}")
        return []

    return await _filter_valid_images(candidates)


# ---------------------------------------------------------------------------
# Backward-compatible single-result wrappers (kept for any remaining callers)
# ---------------------------------------------------------------------------

async def fetch_og_image(url: str) -> str | None:
    """Legacy wrapper: returns the first valid OG image or None."""
    images = await fetch_og_images(url)
    return images[0] if images else None


async def fetch_reddit_image(html_content: str, url: str) -> str | None:
    """Legacy wrapper: returns the first valid Reddit image or None."""
    images = await fetch_reddit_images(html_content, url)
    return images[0] if images else None
