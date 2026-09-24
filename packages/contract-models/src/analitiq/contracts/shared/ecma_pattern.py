"""Building JSON Schema `pattern` strings that mirror a Python check."""
from __future__ import annotations

import re


def case_insensitive_ecma(pattern: str) -> str:
    """Inline case-insensitivity for a JSON Schema `pattern`.

    ECMA-262 `pattern` has no inline `(?i)` flag, so mirror the runtime's
    case folding by expanding each ASCII letter to a two-character class
    (`s` -> `[Ss]`). Lossless only where letters appear solely in literal
    text, never inside a structural construct such as `\\w` or `[a-z]`.
    """
    return re.sub(
        r"[A-Za-z]", lambda m: f"[{m.group().upper()}{m.group().lower()}]", pattern
    )
