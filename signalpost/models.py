"""Data model: facts with evidence, source results, helpers."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Optional

CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass
class Evidence:
    """Where a fact came from and why we believe it belongs to this company."""

    source: str                 # brreg_enheter | brreg_roller | brreg_underenheter | brreg_regnskap | gleif | wikidata | website
    url: str                    # link a reviewer can open to verify the fact
    retrieved_at: str           # when we fetched it (UTC ISO)
    source_date: Optional[str] = None  # date the source itself attaches to the fact (period end, registration date, ...)
    match: str = "orgnr_exact"  # orgnr_exact | registry_declared | orgnr_on_page | name_on_page | unverified
    confidence: str = "high"    # high | medium | low
    note: Optional[str] = None


@dataclass
class Fact:
    key: str
    value: Any
    evidence: list[Evidence]
    first_seen: str = ""
    last_seen: str = ""
    status: str = "active"      # active | retracted
    history: list[dict] = field(default_factory=list)
    stale: bool = False         # source could not be re-checked on the last run

    @property
    def confidence(self) -> str:
        if not self.evidence:
            return "low"
        return min((e.confidence for e in self.evidence), key=lambda c: CONFIDENCE_RANK.get(c, 0))

    @property
    def source(self) -> str:
        return self.evidence[0].source if self.evidence else "unknown"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["confidence"] = self.confidence
        return d

    @staticmethod
    def from_dict(d: dict) -> "Fact":
        allowed = Evidence.__dataclass_fields__
        ev = [Evidence(**{k: v for k, v in e.items() if k in allowed}) for e in d.get("evidence", [])]
        return Fact(
            key=d["key"],
            value=d.get("value"),
            evidence=ev,
            first_seen=d.get("first_seen", ""),
            last_seen=d.get("last_seen", ""),
            status=d.get("status", "active"),
            history=list(d.get("history", [])),
            stale=bool(d.get("stale", False)),
        )


@dataclass
class SourceResult:
    source: str
    url: str
    status: str                 # ok | not_found | deleted | error | skipped | blocked | budget
    retrieved_at: str
    http_status: Optional[int] = None
    facts: list[Fact] = field(default_factory=list)
    checks: list[dict] = field(default_factory=list)    # entity-matching checks: {check, passed, detail}
    rejected: list[dict] = field(default_factory=list)  # facts we refused to publish: {key, reason}
    notes: list[str] = field(default_factory=list)
    requests: int = 0
    data: Any = None            # raw payload for chaining between sources; not persisted

    def summary(self) -> dict:
        return {
            "source": self.source,
            "url": self.url,
            "status": self.status,
            "http_status": self.http_status,
            "retrieved_at": self.retrieved_at,
            "facts": len(self.facts),
            "requests": self.requests,
            "checks": self.checks,
            "rejected": self.rejected,
            "notes": self.notes,
        }


def fact(key: str, value: Any, *, source: str, url: str, retrieved_at: str, source_date: Optional[str] = None,
         match: str = "orgnr_exact", confidence: str = "high", note: Optional[str] = None) -> Fact:
    return Fact(key=key, value=value, evidence=[Evidence(source=source, url=url, retrieved_at=retrieved_at,
                                                         source_date=source_date, match=match,
                                                         confidence=confidence, note=note)])


def clean(value: Any) -> Any:
    """Drop None / empty containers recursively so facts stay compact."""
    if isinstance(value, dict):
        out = {k: clean(v) for k, v in value.items()}
        return {k: v for k, v in out.items() if v not in (None, "", [], {})}
    if isinstance(value, list):
        out = [clean(v) for v in value]
        return [v for v in out if v not in (None, "", [], {})]
    return value
