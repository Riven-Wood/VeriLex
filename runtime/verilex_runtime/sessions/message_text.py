"""Human message text only; JSON metadata and tool arguments are not searchable."""
import json


def message_text(content_json: str) -> str:
    try:
        value = json.loads(content_json)
    except (ValueError, TypeError):
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return value.get("text", "") if isinstance(value.get("text"), str) else ""
    if isinstance(value, list):
        return "".join(block["text"] for block in value
                       if isinstance(block, dict) and block.get("type") == "text"
                       and isinstance(block.get("text"), str))
    return ""


def snippet(text: str, query: str) -> dict:
    # Case folding can expand characters (ß → ss); retain original offsets.
    folded, offsets = [], []
    for index, character in enumerate(text):
        part = character.casefold()
        folded.append(part)
        offsets.extend([index] * len(part))
    start = "".join(folded).find(query.casefold())
    if start < 0:
        return {"text": "", "start": 0, "end": 0}
    first = offsets[start]
    last = offsets[start + len(query.casefold()) - 1] + 1
    left, right = max(0, first - 40), min(len(text), last + 40)
    return {"text": text[left:right], "start": first-left, "end": last-left}
