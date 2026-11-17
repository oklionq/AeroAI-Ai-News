import re

def safe_format(text: str, max_len: int = 4096) -> str:
    if len(text) <= max_len:
        return text
        
    trunc_mark = "...\n\n<i>[Текст обрезан из-за лимитов Telegram]</i>"
    allowed_len = max_len - len(trunc_mark)
    truncated = text[:allowed_len]
    
    # Close open tags
    for tag in ['b', 'i', 'u', 's', 'code', 'pre', 'blockquote', 'a']:
        if truncated.count(f'<{tag}') > truncated.count(f'</{tag}>'):
            truncated += f'</{tag}>'
            
    return truncated + trunc_mark

def safe_caption(text: str, max_len: int = 1024) -> str:
    """
    Гарантирует, что текст поста укладывается в строгий лимит caption Telegram (1024 символа).
    Корректно закрывает открытые HTML-теги.
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
    
    # Корректно закрываем открытые теги
    for tag in ['b', 'i', 'u', 's', 'code', 'pre', 'blockquote', 'a']:
        open_count = len(re.findall(rf'<{tag}[> ]', truncated))
        close_count = len(re.findall(rf'</{tag}>', truncated))
        if open_count > close_count:
            truncated += f'</{tag}>'
            
    return truncated + "..."
