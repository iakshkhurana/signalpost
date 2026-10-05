"""Norwegian organisation number (organisasjonsnummer) helpers.

An orgnr is nine digits; the last digit is a MOD11 check digit computed with
weights 3,2,7,6,5,4,3,2 over the first eight digits.
"""
from __future__ import annotations

import re

_WEIGHTS = (3, 2, 7, 6, 5, 4, 3, 2)


def normalize(value: str) -> str:
    """Strip everything but digits (also drops the 'NO' / 'MVA' decorations)."""
    return re.sub(r"\D", "", value or "")


def is_valid(value: str) -> bool:
    digits = normalize(value)
    if len(digits) != 9 or digits == "000000000":
        return False
    total = sum(int(c) * w for c, w in zip(digits[:8], _WEIGHTS))
    check = 11 - (total % 11)
    if check == 11:
        check = 0
    if check == 10:
        return False
    return check == int(digits[8])


def spaced(digits: str) -> str:
    """923609016 -> '923 609 016' (the form GLEIF and most websites use)."""
    return f"{digits[:3]} {digits[3:6]} {digits[6:]}"


def page_pattern(digits: str) -> re.Pattern[str]:
    """Regex that matches the number as written on web pages: 923609016,
    923 609 016, 923.609.016, NO 923 609 016 MVA, etc."""
    a, b, c = digits[:3], digits[3:6], digits[6:]
    return re.compile(rf"(?<!\d){a}[\s. ]?{b}[\s. ]?{c}(?!\d)")


def parse_many(text: str) -> list[str]:
    """Extract candidate org numbers from free text / a file (one per line or
    separated by commas/whitespace). Invalid numbers are skipped."""
    out: list[str] = []
    seen: set[str] = set()
    for token in re.split(r"[\s,;]+", text):
        token = token.strip()
        if not token or token.startswith("#"):
            continue
        digits = normalize(token)
        if len(digits) == 9 and is_valid(digits) and digits not in seen:
            seen.add(digits)
            out.append(digits)
    return out
