"""Normalisation of raw offer text into typed Offer Master values.

Everything here follows the RenoCred null-not-fabricated-zero convention: when
a value cannot be read out of the source with confidence, the function returns
``None``. It never guesses a zero, never rounds an "up to" into a certainty,
and never invents a cap that the source did not state -- an invented cap is
exactly the failure mode that costs a user money at the till.
"""

import calendar
import datetime as dt
import re
from typing import Any

from extractflow.utils.helpers import sanitize_string

# --------------------------------------------------------------------------
# money
# --------------------------------------------------------------------------

_CURRENCY = r"(?:₹|Rs\.?|INR|rs\.?)"
_NUM = r"\d[\d,]*(?:\.\d+)?"

#: "Rs 1,000" / "₹500" / "INR 15,000" / "Rs.150"
_MONEY_PREFIX = re.compile(
    rf"{_CURRENCY}\s*({_NUM})\s*(lakh|lakhs|lac|cr|crore|k)?\b", re.I
)
#: "1,000 Rs" / "500/-"
_MONEY_SUFFIX = re.compile(rf"({_NUM})\s*(?:{_CURRENCY}|/-)", re.I)

_MULTIPLIER = {
    "k": 1_000,
    "lakh": 100_000,
    "lakhs": 100_000,
    "lac": 100_000,
    "cr": 10_000_000,
    "crore": 10_000_000,
}


def parse_money(text: Any) -> float | None:
    """First rupee amount in ``text``, or None. Handles lakh/crore suffixes."""
    if not isinstance(text, str) or not text.strip():
        return None
    for pattern in (_MONEY_PREFIX, _MONEY_SUFFIX):
        match = pattern.search(text)
        if not match:
            continue
        try:
            value = float(match.group(1).replace(",", ""))
        except ValueError:
            continue
        suffix = match.group(2) if pattern is _MONEY_PREFIX else None
        if suffix:
            value *= _MULTIPLIER.get(suffix.lower(), 1)
        return round(value, 2)
    return None


def parse_all_money(text: Any) -> list[float]:
    """Every rupee amount in ``text``, in order of appearance."""
    if not isinstance(text, str):
        return []
    found: list[float] = []
    for match in _MONEY_PREFIX.finditer(text):
        try:
            value = float(match.group(1).replace(",", ""))
        except ValueError:
            continue
        if match.group(2):
            value *= _MULTIPLIER.get(match.group(2).lower(), 1)
        found.append(round(value, 2))
    return found


# --------------------------------------------------------------------------
# percentages and multipliers
# --------------------------------------------------------------------------

_PERCENT = re.compile(rf"({_NUM})\s*%")
#: "10X reward points", "5x points"
_MULTIPLIER_X = re.compile(r"\b(\d+(?:\.\d+)?)\s*[xX]\b")


def parse_percentage(text: Any) -> float | None:
    if not isinstance(text, str):
        return None
    match = _PERCENT.search(text)
    if not match:
        return None
    try:
        value = float(match.group(1).replace(",", ""))
    except ValueError:
        return None
    # A "percentage" above 100 is almost always a mis-parse of something else
    # (a price, a point count). Refuse it rather than store a nonsense rate.
    return value if 0 <= value <= 100 else None


#: "Up to 20% off", "upto 5% cashback", "as much as 10%", "max 15%".
_UPPER_BOUND = re.compile(
    r"(?:up\s*to|upto|as\s*much\s*as|max(?:imum)?(?:\s*of)?|flat\s*up\s*to)\s*$",
    re.I,
)

RATE_EXACT = "EXACT"
RATE_UP_TO = "UP_TO"


def parse_rate_type(text: Any) -> str | None:
    """Whether a stated rate is a definite rate or only a ceiling.

    "5% Instant Discount" is a rate a user will actually receive; "Up to 20%
    Off" is an upper bound that most transactions will not reach. Treating the
    second as the first is how a recommendation engine confidently promises
    money the user never gets, so the distinction is carried on the record and
    the vocabulary (EXACT / UP_TO) is the one the Card Master already uses.
    """
    if not isinstance(text, str):
        return None
    match = _PERCENT.search(text)
    if not match:
        return None
    return RATE_UP_TO if _UPPER_BOUND.search(text[: match.start()]) else RATE_EXACT


def parse_reward_multiplier(text: Any) -> float | None:
    if not isinstance(text, str):
        return None
    match = _MULTIPLIER_X.search(text)
    if not match:
        return None
    try:
        value = float(match.group(1))
    except ValueError:
        return None
    return value if 1 <= value <= 200 else None


# --------------------------------------------------------------------------
# caps and floors
# --------------------------------------------------------------------------

_MAX_PATTERNS = [
    re.compile(
        rf"max(?:imum)?\.?\s*(?:discount|cashback|benefit|savings?)?"
        rf"\s*[:\-]?\s*(?:of\s*)?"
        rf"({_CURRENCY}\s*{_NUM}(?:\s*(?:lakh|lakhs|lac|k))?)",
        re.I,
    ),
    re.compile(rf"up\s*to\s*({_CURRENCY}\s*{_NUM}(?:\s*(?:lakh|lakhs|lac|k))?)", re.I),
    re.compile(
        rf"capped?\s*at\s*({_CURRENCY}\s*{_NUM}(?:\s*(?:lakh|lakhs|lac|k))?)", re.I
    ),
]

_MIN_PATTERNS = [
    re.compile(
        rf"min(?:imum)?\.?\s*"
        rf"(?:tra?n?s?a?c?t?i?o?n?|trxn|txn|spend|purchase|booking|order"
        rf"|cart|bill)?\s*(?:value|amount|size)?\s*[:\-]?\s*(?:of\s*)?"
        rf"({_CURRENCY}\s*{_NUM}(?:\s*(?:lakh|lakhs|lac|k))?)",
        re.I,
    ),
    re.compile(
        rf"on\s*(?:a\s*)?min(?:imum)?\.?\s*(?:spend|purchase|transaction)"
        rf"\s*of\s*({_CURRENCY}\s*{_NUM})",
        re.I,
    ),
]


def parse_maximum_benefit(text: Any) -> float | None:
    """The stated cap on an offer's value, or None if the source states none."""
    if not isinstance(text, str):
        return None
    for pattern in _MAX_PATTERNS:
        match = pattern.search(text)
        if match:
            value = parse_money(match.group(1))
            if value is not None:
                return value
    return None


def parse_minimum_spend(text: Any) -> float | None:
    if not isinstance(text, str):
        return None
    for pattern in _MIN_PATTERNS:
        match = pattern.search(text)
        if match:
            value = parse_money(match.group(1))
            if value is not None:
                return value
    return None


# --------------------------------------------------------------------------
# dates
# --------------------------------------------------------------------------

_MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}
_MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_name) if m})
_MONTHS.update({"sept": 9})

_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_DMY = re.compile(r"\b(\d{1,2})[\s\-/.]+([A-Za-z]{3,9})[\s\-/.,]+(\d{4})\b")
_DMY_NUM = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b")
_MDY = re.compile(
    r"\b([A-Za-z]{3,9})[\s\-/.]+(\d{1,2})(?:st|nd|rd|th)?[\s\-/.,]+(\d{4})\b"
)
#: "1 Oct - 10 Oct 2025" / "22 Sep - 03 Oct 2025"
_RANGE_SAME_YEAR = re.compile(
    r"\b(\d{1,2})\s*([A-Za-z]{3,9})?\s*(?:-|–|to|till|until)"
    r"\s*(\d{1,2})\s*([A-Za-z]{3,9})\s*(\d{4})\b",
    re.I,
)


def _mk(year: int, month: int, day: int) -> str | None:
    try:
        return dt.date(year, month, day).isoformat()
    except ValueError:
        return None


#: "31 st October", "1st Oct" -- CMS templates routinely split the ordinal
#: suffix into its own <sup>, which survives text extraction as a stray space.
_ORDINAL = re.compile(r"(?<=\d)\s*(?:st|nd|rd|th)\b", re.I)


def parse_date(text: Any) -> str | None:
    """First parseable date in ``text`` as an ISO ``YYYY-MM-DD`` string."""
    if not isinstance(text, str) or not text.strip():
        return None
    text = _ORDINAL.sub("", text)

    match = _ISO.search(text)
    if match:
        return _mk(int(match.group(1)), int(match.group(2)), int(match.group(3)))

    match = _DMY.search(text)
    if match:
        month = _MONTHS.get(match.group(2).lower())
        if month:
            return _mk(int(match.group(3)), month, int(match.group(1)))

    match = _MDY.search(text)
    if match:
        month = _MONTHS.get(match.group(1).lower())
        if month:
            return _mk(int(match.group(3)), month, int(match.group(2)))

    match = _DMY_NUM.search(text)
    if match:
        # Indian sources are day-first; only fall back to month-first when the
        # first component cannot be a day.
        first, second, year = (
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3)),
        )
        if first > 12 >= second or first <= 12:
            return _mk(year, second, first) if first <= 31 else None
    return None


def parse_date_range(text: Any) -> tuple[str | None, str | None]:
    """Parse ``"Validity: 1 Oct - 10 Oct 2025"`` into ``(from, until)``.

    Where only the end month/year is stated, the start inherits them, which is
    how these compressed Indian campaign ranges are written.
    """
    if not isinstance(text, str) or not text.strip():
        return None, None
    text = _ORDINAL.sub("", text)

    match = _RANGE_SAME_YEAR.search(text)
    if match:
        start_day, start_mon, end_day, end_mon, year = match.groups()
        end_month = _MONTHS.get(end_mon.lower())
        start_month = _MONTHS.get((start_mon or end_mon).lower())
        if start_month and end_month:
            year_int = int(year)
            start_year = year_int - 1 if start_month > end_month else year_int
            return (
                _mk(start_year, start_month, int(start_day)),
                _mk(year_int, end_month, int(end_day)),
            )

    # Fall back to "first date ... second date" anywhere in the string.
    parts = re.split(
        r"\s*(?:-|–|to|till|until|through)\s*", text, maxsplit=1, flags=re.I
    )
    if len(parts) == 2:
        start, end = parse_date(parts[0]), parse_date(parts[1])
        if start or end:
            return start, end
    single = parse_date(text)
    if single and re.search(
        r"\b(?:valid\s*(?:till|until|upto|up\s*to)|expires?|ends?)\b", text, re.I
    ):
        return None, single
    return (single, None) if single else (None, None)


# --------------------------------------------------------------------------
# text
# --------------------------------------------------------------------------

_TITLE_NOISE = re.compile(r"\s*[|·•]\s*$")


def clean_text(value: Any) -> str | None:
    """Whitespace-normalise and drop trailing separators; empty becomes None."""
    text = sanitize_string(value)
    if not isinstance(text, str):
        return None
    text = _TITLE_NOISE.sub("", text).strip()
    return text or None


def titleise(value: Any) -> str | None:
    """Turn a hyphen/underscore slug into a display name.

    Words that are already mixed-case or all-caps acronyms keep their casing.
    """
    text = clean_text(value)
    if text is None:
        return None
    if " " in text and not re.search(r"[-_]", text):
        return text
    words = [w for w in re.split(r"[-_\s]+", text) if w]
    out = []
    for word in words:
        if word.isupper() and len(word) <= 4:
            out.append(word)
        elif any(c.isupper() for c in word[1:]):
            out.append(word)
        else:
            out.append(word.capitalize())
    return " ".join(out) or None


def split_list(value: Any, separators: str = ",;|") -> list[str]:
    """Split a delimited source string into clean, de-duplicated tokens."""
    if isinstance(value, (list, tuple, set)):
        raw = list(value)
    elif isinstance(value, str):
        raw = re.split(rf"[{re.escape(separators)}]|\\n", value)
    else:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for item in raw:
        token = clean_text(item)
        if token and token.lower() not in seen:
            seen.add(token.lower())
            out.append(token)
    return out
