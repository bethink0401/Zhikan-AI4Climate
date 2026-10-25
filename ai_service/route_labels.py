"""User-facing names for internal route identifiers; IDs remain unchanged."""

import re

_MODES = {"drive": "驾车", "transit": "公共交通", "walk": "步行"}
_DIGITS = "零一二三四五六七八九"


def _ordinal(number: int) -> str:
    if number < 10:
        return _DIGITS[number]
    if number < 100:
        tens, ones = divmod(number, 10)
        return ("" if tens == 1 else _DIGITS[tens]) + "十" + (_DIGITS[ones] if ones else "")
    return str(number)


def route_display_name(route_id: str) -> str:
    match = re.fullmatch(r"(drive|transit|walk)_(\d{1,3})", route_id)
    if not match:
        return route_id
    return f"{_MODES[match[1]]}方案{_ordinal(int(match[2]) + 1)}"


def readable_route_names(text: str) -> str:
    for mode, label in _MODES.items():
        text = re.sub(
            rf"(?<![A-Za-z0-9_])(?:{label}(?:方案)?\s*)?{mode}_\d{{1,3}}(?![A-Za-z0-9_])",
            lambda match: route_display_name(re.search(rf"{mode}_\d+", match[0])[0]),
            text,
        )
    return text
