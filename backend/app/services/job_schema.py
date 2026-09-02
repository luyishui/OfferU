from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any


_KEYWORD_SPLIT_PATTERN = re.compile(r"[,，;；、\n\r\t]+")


def normalize_job_keywords(value: Any) -> list[str]:
    """Normalize job keywords to the list shape expected by the API contract."""
    if value in (None, ""):
        return []
    if isinstance(value, str):
        candidates = _KEYWORD_SPLIT_PATTERN.split(value)
    elif isinstance(value, (list, tuple, set)):
        candidates = []
        for item in value:
            if item in (None, ""):
                continue
            if isinstance(item, str):
                candidates.extend(_KEYWORD_SPLIT_PATTERN.split(item))
            else:
                candidates.append(str(item))
    else:
        candidates = [str(value)]

    keywords: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        keyword = " ".join(str(candidate).strip().split())
        if not keyword or keyword in seen:
            continue
        keywords.append(keyword)
        seen.add(keyword)
    return keywords


def normalize_job_record_payload(data: Mapping[str, Any]) -> dict[str, Any]:
    normalized = {str(key): value for key, value in data.items()}
    if "keywords" in normalized:
        normalized["keywords"] = normalize_job_keywords(normalized.get("keywords"))
    return normalized
