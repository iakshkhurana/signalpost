from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..http import Http, Tally


@dataclass
class Ctx:
    """Per-company fetch context shared by all source adapters."""
    http: Http
    tally: Tally
    retrieved_at: str
    options: dict[str, Any] = field(default_factory=dict)

    def opt(self, key: str, default: Any = None) -> Any:
        return self.options.get(key, default)


def joined(lines: Any) -> str | None:
    """Brreg stores multi-line text as a list of strings."""
    if isinstance(lines, list):
        text = " ".join(s.strip() for s in lines if isinstance(s, str)).strip()
        return text or None
    if isinstance(lines, str):
        return lines.strip() or None
    return None


def address(a: dict | None) -> dict | None:
    if not a:
        return None
    return {
        "street": [s for s in (a.get("adresse") or []) if s],
        "postal_code": a.get("postnummer"),
        "city": a.get("poststed"),
        "municipality": a.get("kommune"),
        "municipality_number": a.get("kommunenummer"),
        "country": a.get("land"),
        "country_code": a.get("landkode"),
    }


def code(c: dict | None) -> dict | None:
    if not c:
        return None
    return {"code": c.get("kode"), "description": c.get("beskrivelse")}
