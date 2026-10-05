"""Pick a varied, reproducible sample of Norwegian companies from Enhetsregisteret
(used to build the >=1000 submitted profiles)."""
from __future__ import annotations

import random

from .http import Http
from .orgnr import is_valid
from .sources.brreg import search

# (share of sample, search filters). Mix of sizes and legal forms so the agent is
# exercised on large groups, SMEs, sole proprietorships, foundations, foreign branches...
STRATA = [
    (0.30, {"organisasjonsform": "AS", "fraAntallAnsatte": 50}),
    (0.25, {"organisasjonsform": "AS", "fraAntallAnsatte": 5, "tilAntallAnsatte": 49}),
    (0.10, {"organisasjonsform": "AS", "fraAntallAnsatte": 1, "tilAntallAnsatte": 4}),
    (0.08, {"organisasjonsform": "ASA"}),
    (0.07, {"organisasjonsform": "ENK", "fraAntallAnsatte": 1}),
    (0.05, {"organisasjonsform": "STI", "fraAntallAnsatte": 1}),
    (0.05, {"organisasjonsform": "SA", "fraAntallAnsatte": 1}),
    (0.04, {"organisasjonsform": "NUF", "fraAntallAnsatte": 1}),
    (0.03, {"organisasjonsform": "ANS,DA", "fraAntallAnsatte": 1}),
    (0.03, {"organisasjonsform": "KS,BA,IKS", "fraAntallAnsatte": 1}),
]
PAGE = 200


async def discover(http: Http, count: int = 1000, seed: int = 20261005, verbose=print) -> list[str]:
    rng = random.Random(seed)
    picked: list[str] = []
    seen: set[str] = set()
    for share, params in STRATA:
        want = max(1, round(count * share))
        first = await search(http, params, page=0, size=PAGE)
        page_info = first.get("page") or {}
        total_pages = min(int(page_info.get("totalPages") or 1), 10000 // PAGE)
        pool = list(_orgnrs(first))
        pages_needed = max(0, (want * 3) // PAGE)  # oversample a few pages, then sample randomly
        for pg in rng.sample(range(1, total_pages), min(pages_needed, max(0, total_pages - 1))) if total_pages > 1 else []:
            pool.extend(_orgnrs(await search(http, params, page=pg, size=PAGE)))
        pool = [o for o in dict.fromkeys(pool) if o not in seen]
        rng.shuffle(pool)
        chosen = pool[:want]
        picked.extend(chosen)
        seen.update(chosen)
        verbose(f"  {params}: {len(chosen)} picked (pool {len(pool)}, {page_info.get('totalElements')} total)")
    rng.shuffle(picked)
    return picked[:count] if len(picked) >= count else picked


def _orgnrs(page: dict):
    for e in (page.get("_embedded") or {}).get("enheter") or []:
        o = e.get("organisasjonsnummer")
        if o and is_valid(o) and not e.get("konkurs"):
            yield o
