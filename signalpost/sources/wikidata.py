"""Wikidata (CC0) looked up by property P2333 'Norwegian organisation number'.
Third-party descriptive data: matched exactly on the org number, but values
such as employee counts are published at medium confidence."""
from __future__ import annotations

from urllib.parse import urlencode

from ..models import SourceResult, fact, clean
from .base import Ctx

ENDPOINT = "https://query.wikidata.org/sparql"

QUERY = """
SELECT ?item ?itemLabel ?itemDescription ?modified
  (GROUP_CONCAT(DISTINCT ?website; separator="|") AS ?websites)
  (SAMPLE(?inception) AS ?inception)
  (GROUP_CONCAT(DISTINCT ?hqLabel; separator="|") AS ?hqs)
  (GROUP_CONCAT(DISTINCT ?industryLabel; separator="|") AS ?industries)
  (SAMPLE(?employees) AS ?employees)
  (SAMPLE(?logo) AS ?logo)
  (SAMPLE(?en) AS ?en) (SAMPLE(?nb) AS ?nb)
  (GROUP_CONCAT(DISTINCT ?parentLabel; separator="|") AS ?parents)
  (GROUP_CONCAT(DISTINCT ?ownerLabel; separator="|") AS ?owners)
  (GROUP_CONCAT(DISTINCT ?ceoLabel; separator="|") AS ?ceos)
  (GROUP_CONCAT(DISTINCT ?ticker; separator="|") AS ?tickers)
WHERE {
  ?item wdt:P2333 "%s" .
  ?item schema:dateModified ?modified .
  OPTIONAL { ?item wdt:P856 ?website }
  OPTIONAL { ?item wdt:P571 ?inception }
  OPTIONAL { ?item wdt:P159 ?hq . ?hq rdfs:label ?hqLabel FILTER(LANG(?hqLabel) = "en") }
  OPTIONAL { ?item wdt:P452 ?industry . ?industry rdfs:label ?industryLabel FILTER(LANG(?industryLabel) = "en") }
  OPTIONAL { ?item wdt:P1128 ?employees }
  OPTIONAL { ?item wdt:P154 ?logo }
  OPTIONAL { ?item wdt:P749 ?parent . ?parent rdfs:label ?parentLabel FILTER(LANG(?parentLabel) = "en") }
  OPTIONAL { ?item wdt:P127 ?owner . ?owner rdfs:label ?ownerLabel FILTER(LANG(?ownerLabel) = "en") }
  OPTIONAL { ?item wdt:P169 ?ceo . ?ceo rdfs:label ?ceoLabel FILTER(LANG(?ceoLabel) = "en") }
  OPTIONAL { ?item wdt:P249 ?ticker }
  OPTIONAL { ?en schema:about ?item ; schema:isPartOf <https://en.wikipedia.org/> }
  OPTIONAL { ?nb schema:about ?item ; schema:isPartOf <https://no.wikipedia.org/> }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en,nb,no". }
}
GROUP BY ?item ?itemLabel ?itemDescription ?modified
LIMIT 3
"""


def _split(v: str | None) -> list[str]:
    return [p for p in (v or "").split("|") if p]


async def fetch_wikidata(ctx: Ctx, orgnr: str) -> SourceResult:
    url = ENDPOINT + "?" + urlencode({"format": "json", "query": QUERY % orgnr})
    before = ctx.tally.requests
    resp = await ctx.http.get(url, headers={"Accept": "application/sparql-results+json"}, tally=ctx.tally)
    res = SourceResult(source="wikidata", url=url, status="error", retrieved_at=ctx.retrieved_at,
                       http_status=resp.status, requests=ctx.tally.requests - before)
    if resp.error:
        res.notes.append(resp.error)
        return res
    if not resp.ok:
        res.notes.append(f"unexpected HTTP {resp.status}")
        return res
    try:
        rows = (resp.json() or {}).get("results", {}).get("bindings", [])
    except ValueError:
        res.notes.append("non-JSON response")
        return res
    if not rows:
        res.status = "not_found"
        res.notes.append("no Wikidata item has P2333 = this org number")
        return res
    res.status = "ok"
    row = {k: v.get("value") for k, v in rows[0].items()}
    if len(rows) > 1:
        res.notes.append(f"{len(rows)} Wikidata items share this org number; using the first")
    qid = (row.get("item") or "").rsplit("/", 1)[-1]
    page = f"https://www.wikidata.org/wiki/{qid}"
    modified = (row.get("modified") or "")[:10] or None
    res.checks.append({"check": "wikidata_p2333", "passed": True,
                       "detail": f"{qid} has P2333 (Norwegian organisation number) = {orgnr}"})
    F = lambda key, value, confidence="high", **kw: res.facts.append(fact(  # noqa: E731
        key, clean(value), source="wikidata", url=page, retrieved_at=ctx.retrieved_at,
        source_date=kw.pop("source_date", modified), confidence=confidence, **kw))
    F("wikidata.id", qid)
    F("wikidata.label", row.get("itemLabel"))
    if row.get("itemDescription"):
        F("wikidata.description", row["itemDescription"])
    if row.get("en"):
        F("wikipedia.en", row["en"])
    if row.get("nb"):
        F("wikipedia.no", row["nb"])
    if _split(row.get("websites")):
        F("wikidata.websites", _split(row["websites"]), confidence="medium")
    if row.get("inception"):
        F("wikidata.inception", row["inception"][:10], confidence="medium", source_date=row["inception"][:10])
    if _split(row.get("hqs")):
        F("wikidata.headquarters", _split(row["hqs"]), confidence="medium")
    if _split(row.get("industries")):
        F("wikidata.industries", _split(row["industries"]), confidence="medium")
    if row.get("employees"):
        try:
            F("wikidata.employees", int(float(row["employees"])), confidence="medium",
              note="third-party figure; prefer the registry employee count")
        except ValueError:
            pass
    if row.get("logo"):
        F("wikidata.logo", row["logo"])
    if _split(row.get("parents")):
        F("wikidata.parent_organizations", _split(row["parents"]), confidence="medium")
    if _split(row.get("owners")):
        F("wikidata.owned_by", _split(row["owners"]), confidence="medium")
    if _split(row.get("ceos")):
        F("wikidata.ceo", _split(row["ceos"]), confidence="medium", note="may lag the registry's daglig leder")
    if _split(row.get("tickers")):
        F("wikidata.tickers", _split(row["tickers"]), confidence="medium")
    return res
