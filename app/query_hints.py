"""Whole-word query hints, preferring longer phrases over overlapping words."""
import re
import unicodedata


CCTV_VI_EN_VOCAB = {
    "tai nạn": "traffic accident collision crash",
    "va chạm": "vehicle collision impact",
    "tắc đường": "traffic jam congestion slow traffic",
    "kẹt xe": "traffic jam queue congestion",
    "xe máy": "motorcycle motorbike",
    "ô tô": "car automobile",
    "xe hơi": "car automobile",
    "xe buýt": "bus transit bus",
    "xe tải": "truck cargo vehicle",
    "xe đạp": "bicycle cyclist",
    "ngã tư": "road intersection crossroad",
    "ngã ba": "T-junction intersection",
    "đường phố": "city street road",
    "ban đêm": "night nighttime dark",
    "ban ngày": "daytime daylight",
    "trời mưa": "rain rainy wet road",
    "cảnh sát": "police officer patrol car",
    "dừng lại": "stopped vehicle stationary",
    "quay đầu": "u-turn turning vehicle",
}

_BILINGUAL_PATTERN = re.compile(
    r'(?<!\w)(?:'
    + '|'.join(re.escape(term) for term in sorted(CCTV_VI_EN_VOCAB, key=lambda w: (-len(w), w)))
    + r')(?!\w)'
)


def expand_bilingual_query(query: str) -> tuple[str, list[str]]:
    """Translate known CCTV phrases, not arbitrary Vietnamese or visual semantics."""
    normalized = unicodedata.normalize('NFC', query).casefold()
    matched = []
    seen = set()

    def replace(match):
        term = match.group()
        if term not in seen:
            matched.append(term)
            seen.add(term)
        return CCTV_VI_EN_VOCAB[term]

    prompt = _BILINGUAL_PATTERN.sub(replace, normalized)
    return (prompt, matched) if matched else (query, [])

def keyword_hints(text, vocabulary):
    normalized = unicodedata.normalize('NFC', text or '').casefold()
    words = {unicodedata.normalize('NFC', word).casefold(): value
             for word, value in vocabulary.items() if word}
    if not words:
        return set()
    alternatives = '|'.join(re.escape(word) for word in sorted(words, key=lambda w: (-len(w), w)))
    return {words[match.group()] for match in re.finditer(r'(?<!\w)(?:' + alternatives + r')(?!\w)', normalized)}
