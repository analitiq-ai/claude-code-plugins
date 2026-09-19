"""RE2, the regular-expression dialect the contract's patterns are matched in."""
from __future__ import annotations

from typing import Any

import re2

# A refused pattern is reported as a finding; RE2 would also write it to stderr.
_OPTIONS = re2.Options()
_OPTIONS.log_errors = False


def compile_re2(pattern: str) -> Any:
    """`pattern` compiled in RE2.

    ValueError reading `is not valid RE2 (…)` when RE2 cannot take it, carrying
    RE2's own parse error; the caller names what was refused."""
    try:
        return re2.compile(pattern, options=_OPTIONS)
    except re2.error as exc:
        detail = exc.args[0] if exc.args else exc
        text = detail.decode("utf-8", "replace") if isinstance(detail, bytes) else str(detail)
        raise ValueError(f"is not valid RE2 ({text})") from exc
    except UnicodeEncodeError as exc:
        # RE2 reads UTF-8, and a lone surrogate (which JSON can spell) has none.
        raise ValueError(f"is not valid RE2 ({exc.reason} at {exc.start})") from exc
