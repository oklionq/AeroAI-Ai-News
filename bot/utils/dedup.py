import re
import hashlib

def normalize_title(title: str) -> str:
    if not title:
        return ""
    clean = title.lower()
    clean_no_punct = re.sub(r'[^\w\s]', '', clean)
    stop_words = {'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by'}
    words = [w for w in clean_no_punct.split() if w not in stop_words]
    res = " ".join(words).strip()
    if not res:
        # Fallback to stripped original lowercased so emoji-only doesn't collapse to empty string
        return clean.strip()
    return res

def generate_hash(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()

def get_url_hash(url: str) -> str:
    from urllib.parse import urlparse
    parsed = urlparse(url)
    # Ignore scheme and query params for deduplication
    core_url = f"{parsed.netloc}{parsed.path}"
    return generate_hash(core_url)

def title_similarity(t1: str, t2: str) -> float:
    words1 = set(t1.split())
    words2 = set(t2.split())
    if not words1 or not words2:
        return 0.0
    intersection = words1.intersection(words2)
    return len(intersection) / max(len(words1), len(words2))
