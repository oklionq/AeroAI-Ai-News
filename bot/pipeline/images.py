import os
from PIL import Image
from bot.config import config
from bot.utils.logger import logger

VIDEO_EXTENSIONS = ('.mp4', '.mov', '.webm', '.mkv')
IMAGE_EXTENSIONS = ('.jpg', '.jpeg', '.png', '.webp')

def is_video_file(file_path: str) -> bool:
    """Определяет, является ли файл видео по расширению."""
    return file_path.lower().endswith(VIDEO_EXTENSIONS)

def validate_media_file(file_path: str) -> bool:
    """Проверяет локальный файл медиа (фото или видео) на корректность и размер."""
    if not os.path.exists(file_path):
        return False
        
    lower = file_path.lower()
    if lower.endswith(VIDEO_EXTENSIONS):
        # Проверяем, что видеофайл существует и не пустой (> 10 КБ)
        try:
            size = os.path.getsize(file_path)
            return size > 10240
        except Exception:
            return False
            
    # Проверка изображения через Pillow
    try:
        with Image.open(file_path) as img:
            w, h = img.size
            if w < config.min_image_width and h < config.min_image_width:
                logger.info(f"Image too small ({w}x{h}px, min {config.min_image_width}px): {file_path}")
                return False
            return True
    except Exception as e:
        logger.warning(f"Pillow could not open image {file_path}: {e}")
        return False

def filter_valid_local_images(paths: list[str]) -> list[str]:
    """Фильтрует список локальных путей к медиафайлам (фото и видео)."""
    valid = []
    for p in paths:
        if validate_media_file(p):
            valid.append(p)
            if len(valid) >= 4:
                break
    return valid
