"""Characters forbidden in user-supplied text (2026-09-16 audit, INJ-3/INJ-4).

Two layers share this module:

* prose fields (``PostgresText``) still allow ``\\t\\n\\r`` for narrative text
  but must reject characters that are invisible, act as line breaks in
  viewers/exports, or reverse the visual order of surrounding text;
* identifier fields (``no_control_characters``) additionally reject tabs and
  line breaks outright.

The set is deliberately conservative about legitimate Indic input: ZWJ/U+200C
and ZWNJ/U+200D are REQUIRED for Telugu conjuncts and stay allowed. Everything
below is either a control (DEL, C1), a line/paragraph separator that forges
record boundaries in logs and line-oriented viewers (U+2028/U+2029, NEL
U+0085), a bidirectional embedding/override/isolate control usable for visual
spoofing (U+202A–U+202E, U+2066–U+2069, LRM U+200E, RLM U+200F), or an
invisible no-break space (BOM/U+FEFF).
"""

from __future__ import annotations

# DEL (0x7F) plus the whole C1 range (0x80–0x9F, includes NEL 0x85 and CSI 0x9B).
_CONTROL_RANGE = frozenset(chr(code) for code in range(0x7F, 0xA0))

# Unicode line/paragraph separators: not Cc, but line-break-like in viewers.
_LINE_SEPARATORS = frozenset("\u2028\u2029")

# Bidirectional embedding/override marks and isolates (visual-order spoofing).
_BIDI_CONTROLS = frozenset(chr(code) for code in (*range(0x202A, 0x202F), *range(0x2066, 0x206A)))

# Invisible direction marks and the byte-order mark.
_INVISIBLE_MARKS = frozenset("\u200e\u200f\ufeff")

#: Characters no user-supplied text field may carry.
FORBIDDEN_TEXT_CHARS: frozenset[str] = (
    _CONTROL_RANGE | _LINE_SEPARATORS | _BIDI_CONTROLS | _INVISIBLE_MARKS
)

#: Characters that must never appear single-line (headings, DPR title lines):
#: the FORBIDDEN set plus the ASCII/C0 line breaks and NEL.
SINGLE_LINE_BREAK_CHARS: frozenset[str] = FORBIDDEN_TEXT_CHARS | frozenset("\r\n")


def sanitize_single_line(value: str, *, replacement: str = " ") -> str:
    """Collapse every line-break-like / forbidden character to `replacement`.

    Used for strings interpolated into generated documents (the DPR loan
    file): a plan name carrying ``\\r\\n`` or U+2028 could otherwise forge
    additional document sections or log-looking lines inside the artifact
    (2026-09-16 audit, INJ-1).
    """
    cleaned = "".join(replacement if char in SINGLE_LINE_BREAK_CHARS else char for char in value)
    if replacement == " ":
        # Collapse the runs the replacements created ("a\r\n\r\nb" -> "a b").
        while "  " in cleaned:
            cleaned = cleaned.replace("  ", " ")
    return cleaned.strip()
