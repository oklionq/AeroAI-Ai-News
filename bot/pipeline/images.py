import asyncio
import cloudscraper
import httpx
from bs4 import BeautifulSoup
from bot.utils.logger import logger

def _sync_fetch_html(url: str) -> tuple[int, bytes]:
    scraper = cloudscraper.create_scraper()
    resp = scraper.get(url, timeout=15)
    return resp.status_code, resp.content

async def fetch_og_image(url: str) -> str | None:
    try:
        status_code, content = await asyncio.to_thread(_sync_fetch_html, url)
        if status_code != 200:
            logger.warning(f"Failed to fetch {url}, status code: {status_code}")
            return None
            
        soup = BeautifulSoup(content, 'html.parser')
        img_url = None
        
        og_image = soup.find('meta', property='og:image')
        if og_image and og_image.get('content'):
            img_url = og_image['content']
        else:
            twitter_image = soup.find('meta', attrs={'name': 'twitter:image'})
            if twitter_image and twitter_image.get('content'):
                img_url = twitter_image['content']
                
        if img_url:
            async with httpx.AsyncClient(timeout=15.0) as client:
                headers = {"User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"}
                head_resp = await client.head(img_url, follow_redirects=True, headers=headers)
                content_type = head_resp.headers.get("Content-Type", "")
                content_length = int(head_resp.headers.get("Content-Length", 0))
                
                if content_type.startswith("image/") or img_url.lower().split("?")[0].endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")):
                    logger.info(f"Found image for {url}: {img_url}")
                    return img_url
                else:
                    logger.warning(f"Image validation failed for {img_url}: type={content_type}, size={content_length}")
        else:
            logger.warning(f"No og:image or twitter:image found for {url}")
            
    except Exception as e:
        logger.error(f"Failed to fetch image for {url}: {e}")
        
    return None

import html

async def _validate_image_url(img_url: str) -> str | None:
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            headers = {"User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"}
            head_resp = await client.head(img_url, follow_redirects=True, headers=headers)
            content_type = head_resp.headers.get("Content-Type", "")
            content_length = int(head_resp.headers.get("Content-Length", 0))
            
            if content_type.startswith("image/") or img_url.lower().split("?")[0].endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")):
                return img_url
            else:
                logger.warning(f"Image validation failed for {img_url}: type={content_type}, size={content_length}")
    except Exception as e:
        logger.error(f"Failed to validate image {img_url}: {e}")
    return None

async def fetch_reddit_image(html_content: str, url: str) -> str | None:
    try:
        soup = BeautifulSoup(html_content, 'html.parser')
        for img in soup.find_all('img'):
            src = img.get('src', '')
            if 'preview.redd.it' in src or 'i.redd.it' in src or 'external-preview.redd.it' in src:
                unescaped_src = html.unescape(src)
                logger.info(f"Found Reddit image candidate: {unescaped_src}")
                valid_url = await _validate_image_url(unescaped_src)
                if valid_url:
                    return valid_url
    except Exception as e:
        logger.error(f"Error parsing Reddit HTML for image: {e}")
        
    logger.info(f"No valid Reddit image found in content for {url}")
    return None
