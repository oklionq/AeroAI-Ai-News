def safe_format(text: str, max_len: int = 4096) -> str:
    if len(text) <= max_len:
        return text
        
    trunc_mark = "...\n\n<i>[Текст обрезан из-за лимитов Telegram. Подробности по ссылке в источнике]</i>"
    allowed_len = max_len - len(trunc_mark)
    truncated = text[:allowed_len]
    
    # Naively close open tags
    for tag in ['b', 'i', 'u', 's', 'code', 'pre', 'blockquote', 'a']:
        if truncated.count(f'<{tag}') > truncated.count(f'</{tag}>'):
            truncated += f'</{tag}>'
            
    return truncated + trunc_mark
