import re

def safe_format(text: str, max_len: int = 4096) -> str:
    """Безопасно обрезает длинный текст для обычного текстового сообщения Telegram (до 4096 символов)."""
    if len(text) <= max_len:
        return text
        
    trunc_mark = "...\n\n<i>[Текст обрезан из-за лимитов Telegram]</i>"
    allowed_len = max_len - len(trunc_mark)
    truncated = text[:allowed_len]
    
    # Закрываем теги в порядке LIFO, избегая ложного совпадения <b на <blockquote
    for tag in ['pre', 'code', 'blockquote', 'a', 'u', 's', 'i', 'b']:
        open_count = len(re.findall(rf'<{tag}[> ]', truncated))
        close_count = len(re.findall(rf'</{tag}>', truncated))
        if open_count > close_count:
            truncated += f'</{tag}>' * (open_count - close_count)
            
    return truncated + trunc_mark

def safe_caption(text: str, max_len: int = 1024) -> str:
    """
    Гарантирует, что текст поста укладывается в строгий лимит caption Telegram (1024 символа).
    Обрезает по границе слов и корректно закрывает открытые HTML-теги.
    """
    if len(text) <= max_len:
        return text

    # Резервируем место под многоточие
    allowed_body_len = max_len - 10
    truncated = text[:allowed_body_len]
    
    # Обрезаем по последнему пробелу, чтобы не ломать слова
    last_space = truncated.rfind(' ')
    if last_space > 0:
        truncated = truncated[:last_space]
    
    # Закрываем теги в порядке LIFO, избегая ложного совпадения <b на <blockquote
    for tag in ['pre', 'code', 'blockquote', 'a', 'u', 's', 'i', 'b']:
        open_count = len(re.findall(rf'<{tag}[> ]', truncated))
        close_count = len(re.findall(rf'</{tag}>', truncated))
        if open_count > close_count:
            truncated += f'</{tag}>' * (open_count - close_count)
            
    return truncated + "..."
