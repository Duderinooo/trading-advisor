"""Token-saving JSON helpers shared across prompt-builder + handlers.

Strips None/empty values recursively (Claude doesn't need them, costs tokens).
Uses compact `(",", ":")` separators + ensure_ascii=False to keep UTF-8 (Umlaute)
intact instead of `\\uXXXX`-escaping them.
"""

import json


def compact(d):
    """Strip None and empty-string values recursively. Shrinks input tokens."""
    if isinstance(d, dict):
        return {k: compact(v) for k, v in d.items() if v is not None and v != ""}
    if isinstance(d, list):
        return [compact(x) for x in d]
    return d


def dump(d) -> str:
    """Compact JSON: no indent, no spaces, None stripped, UTF-8 preserved."""
    return json.dumps(compact(d), separators=(",", ":"), ensure_ascii=False)
