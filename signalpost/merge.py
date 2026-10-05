"""Update logic: merge freshly fetched facts into an existing profile.

Rules
- new key                       -> added (first_seen = now)
- same key, different value     -> changed (old value kept in history)
- same key, same value          -> last_seen = now
- old key missing, source fetched OK or reported not_found -> retracted
- old key missing, source errored / skipped / out of budget -> kept, marked stale
Retracted facts stay in the profile (status=retracted) so consumers can see
what used to be true and when it stopped being reported.
"""
from __future__ import annotations

import json
from typing import Iterable

from .models import Fact

VERIFIED_STATUSES = {"ok", "not_found", "deleted"}


def _canon(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def merge(existing: list[Fact], fresh: Iterable[Fact], source_status: dict[str, str], now: str) -> tuple[list[Fact], list[dict]]:
    changes: list[dict] = []
    old_by_key = {f.key: f for f in existing}
    out: list[Fact] = []
    seen: set[str] = set()

    for nf in fresh:
        seen.add(nf.key)
        old = old_by_key.get(nf.key)
        if old is None:
            nf.first_seen = nf.last_seen = now
            out.append(nf)
            changes.append({"at": now, "key": nf.key, "type": "added", "new": nf.value})
            continue
        nf.first_seen = old.first_seen or now
        nf.last_seen = now
        nf.history = list(old.history)
        if old.status == "retracted":
            changes.append({"at": now, "key": nf.key, "type": "reactivated", "old": old.value, "new": nf.value})
            if _canon(old.value) != _canon(nf.value):
                nf.history.append({"value": old.value, "until": old.last_seen})
        elif _canon(old.value) != _canon(nf.value):
            nf.history.append({"value": old.value, "until": old.last_seen})
            changes.append({"at": now, "key": nf.key, "type": "changed", "old": old.value, "new": nf.value})
        nf.history = nf.history[-20:]
        out.append(nf)

    for key, old in old_by_key.items():
        if key in seen:
            continue
        status = source_status.get(old.source, "skipped")
        if status in VERIFIED_STATUSES:
            if old.status != "retracted":
                old.status = "retracted"
                old.history = (old.history + [{"value": old.value, "until": old.last_seen}])[-20:]
                changes.append({"at": now, "key": key, "type": "retracted", "old": old.value})
            out.append(old)
        else:
            old.stale = True
            out.append(old)
    out.sort(key=lambda f: f.key)
    return out, changes
