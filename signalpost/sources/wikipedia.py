"""Wikipedia article summaries (REST API) for the articles Wikidata links to an
item that carries this organisation number (P2333)."""
from __future__ import annotations

import re
from urllib.parse import unquote, quote

from ..models import SourceResult, fact, clean
from .base import Ctx

SUMMARY = "https://{lang}.wikipedia.org/api/rest_v1/page/summary/{title}"


def _title(url: str) -> tuple[str, str] | None:
    m = re.match(r"https?://([a-z\-]+)\.wikipedia\.org/wiki/(.+)$", url or "")
    if not m:
        return None
    return m.group(1), unquote(m.group(2)).split("#")[0]


async def fetch_wikipedia(ctx: Ctx, orgnr: str, article_urls: list[str]) -> SourceResult:
    res = SourceResult(source="wikipedia", url="", status="skipped", retrieved_at=ctx.retrieved_at)
    targets = []
    for u in article_urls:
        t = _title(u)
        if t:
            targets.append((t, u))
    if not targets:
        res.notes.append("no Wikipedia article linked from Wikidata")
        return res
    before = ctx.tally.requests
    try:
        got = 0
        for (lang, title), page_url in targets[:2]:
            api = SUMMARY.format(lang=lang, title=quote(title.replace(" ", "_"), safe=""))
            res.url = res.url or api
            resp = await ctx.http.get(api, headers={"Accept": "application/json"}, tally=ctx.tally)
            res.http_status = resp.status
            if resp.error or not resp.ok:
                res.notes.append(f"{lang}: " + (resp.error or f"HTTP {resp.status}"))
                continue
            d = resp.json() or {}
            if d.get("type") == "disambiguation":
                res.notes.append(f"{lang}: disambiguation page skipped")
                continue
            got += 1
            key = "no" if lang in ("nb", "no", "nn") else lang
            ts = (d.get("timestamp") or "")[:10] or None
            res.facts.append(fact(f"wikipedia.{key}.summary", clean({
                "title": d.get("title"), "description": d.get("description"),
                "extract": (d.get("extract") or "")[:1200] or None,
                "thumbnail": ((d.get("thumbnail") or {}).get("source")),
                "last_edited": ts,
            }), source=res.source, url=page_url, retrieved_at=ctx.retrieved_at, source_date=ts,
                match="wikidata_p2333", confidence="medium",
                note="article is linked from the Wikidata item carrying this organisation number; encyclopaedic text, not a registry fact"))
        res.status = "ok" if got else "error"
        if got:
            res.checks.append({"check": "linked_via_wikidata", "passed": True,
                               "detail": "article URL taken from the Wikidata item whose P2333 equals the org number"})
    finally:
        res.requests = ctx.tally.requests - before
    return res
