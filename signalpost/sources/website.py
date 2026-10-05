"""Company website: the URL the company itself declared to Enhetsregisteret.

We fetch robots.txt, the home page and (optionally) one "about" page. Facts
extracted from the page are only published when the page itself ties back to
the company: the org number printed on the page (high) or the company name
(medium). Otherwise the page-derived facts are rejected and listed as such.
"""
from __future__ import annotations

import json
import re
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from ..models import SourceResult, fact, clean, CONFIDENCE_RANK
from ..orgnr import page_pattern
from .base import Ctx

SOCIAL = {
    "linkedin": re.compile(r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/(?:company|school|showcase)/[\w\-%.]+/?", re.I),
    "facebook": re.compile(r"https?://(?:www\.|m\.)?facebook\.com/(?!sharer|share|dialog|plugins|tr\b|login)[\w\-.]+/?", re.I),
    "instagram": re.compile(r"https?://(?:www\.)?instagram\.com/(?!p/|explore|accounts)[\w\-.]+/?", re.I),
    "x": re.compile(r"https?://(?:www\.)?(?:twitter|x)\.com/(?!intent|share|home|search|hashtag)[\w]+/?", re.I),
    "youtube": re.compile(r"https?://(?:www\.)?youtube\.com/(?:@[\w\-.]+|c/[\w\-.]+|channel/[\w\-]+|user/[\w\-]+)/?", re.I),
    "tiktok": re.compile(r"https?://(?:www\.)?tiktok\.com/@[\w\-.]+/?", re.I),
}
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", re.I)
ABOUT_RE = re.compile(r"\b(om oss|om selskapet|om bedriften|om firmaet|about us|about|om [a-zæøå]+|who we are|company)\b", re.I)
PARKED_RE = re.compile(r"(domain (is )?for sale|parked (domain|page)|dette domenet|domenet er (parkert|til salgs)|buy this domain|"
                       r"under construction|kommer snart|coming soon|website is (temporarily )?unavailable|account suspended)", re.I)
LEGAL_SUFFIX_RE = re.compile(r"\b(asa|as|a/s|ans|da|enk|nuf|sa|ba|ks|iks|fkf|kf|stiftelsen|stiftelse|sf)\b\.?", re.I)


def normalize_url(raw: str | None) -> str | None:
    if not raw:
        return None
    first = re.split(r"[\s,;]+", raw.strip())[0].strip().strip(".,;")
    if not first or "@" in first:
        return None
    if not re.match(r"^[a-z][a-z0-9+.-]*://", first, re.I):
        first = "https://" + first
    parts = urlsplit(first)
    host = parts.netloc.lower()
    if "." not in host or " " in host:
        return None
    return urlunsplit((parts.scheme.lower(), host, parts.path or "/", parts.query, ""))


_FOLD = str.maketrans({"æ": "ae", "ø": "o", "å": "a", "é": "e", "è": "e", "ü": "u", "ö": "o", "ä": "a"})


def _name_variants(name: str | None) -> list[str]:
    if not name:
        return []
    base = name.casefold()
    stripped = LEGAL_SUFFIX_RE.sub(" ", base)
    stripped = re.sub(r"\s+", " ", stripped).strip()
    out = [base]
    if stripped and stripped != base and len(stripped) >= 4:
        out.append(stripped)
    return out


def _name_matches_domain(name: str | None, host: str) -> bool:
    """'ADM AS' vs adm.no, '4SUBSEA AS' vs 4subsea.com, 'Bilkollektivet SA' vs bilkollektivet.no."""
    if not name:
        return False
    label = host.lower().removeprefix("www.").split(".")[0]
    label = re.sub(r"[^a-z0-9]", "", label)
    stripped = LEGAL_SUFFIX_RE.sub(" ", name.casefold()).translate(_FOLD)
    tokens = [re.sub(r"[^a-z0-9]", "", t) for t in stripped.split()]
    tokens = [t for t in tokens if t]
    compact = "".join(tokens)
    if len(label) < 3 or len(compact) < 3:
        return False
    if compact == label:
        return True                                   # 'bilkollektivet sa' vs bilkollektivet.no
    if tokens and len(tokens[0]) >= 4 and tokens[0] == label:
        return True                                   # 'kjell & co norway as' vs kjell.com (brand = first word)
    return len(label) >= 6 and compact.startswith(label)   # 'havila subsea crewing' vs havilasubsea.no


def _jsonld_orgs(soup: BeautifulSoup) -> list[dict]:
    found: list[dict] = []
    for tag in soup.find_all("script", type=re.compile(r"ld\+json", re.I)):
        try:
            data = json.loads(tag.string or tag.get_text() or "")
        except Exception:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                if "@graph" in item and isinstance(item["@graph"], list):
                    stack.extend(item["@graph"])
                t = item.get("@type")
                types = t if isinstance(t, list) else [t]
                if any(isinstance(x, str) and x.endswith(("Organization", "Corporation", "LocalBusiness")) for x in types):
                    found.append(item)
    return found


async def fetch_website(ctx: Ctx, orgnr: str, declared_url: str | None, company_name: str | None,
                        *, declared_by: str = "brreg_enheter") -> SourceResult:
    res = SourceResult(source="website", url=declared_url or "", status="skipped", retrieved_at=ctx.retrieved_at)
    url = normalize_url(declared_url)
    if not url:
        res.notes.append("no usable website declared")
        return res
    res.url = url
    before = ctx.tally.requests
    try:
        if not await ctx.http.allowed_by_robots(url, tally=ctx.tally):
            res.status = "blocked"
            res.notes.append("robots.txt disallows fetching this page for our user agent")
            return res
        resp = await ctx.http.get(url, tally=ctx.tally, max_bytes=1_500_000)
        if (resp.error or (resp.status and resp.status >= 400)) and url.startswith("https://"):
            alt = "http://" + url[len("https://"):]
            resp2 = await ctx.http.get(alt, tally=ctx.tally, max_bytes=1_500_000)
            if resp2.ok:
                resp = resp2
        res.http_status = resp.status
        if resp.error:
            res.status = "error"
            res.notes.append(resp.error)
            return res
        if not resp.ok:
            res.status = "error"
            res.notes.append(f"HTTP {resp.status}")
            return res
        ctype = (resp.headers.get("content-type") or "").lower()
        if ctype and "html" not in ctype and "xml" not in ctype and "text" not in ctype:
            res.status = "error"
            res.notes.append(f"not an HTML page ({ctype})")
            return res
        final_url = resp.final_url
        soup = BeautifulSoup(resp.text or "", "html.parser")
        for t in soup(["script", "style", "noscript", "svg"]):
            if t.name == "script" and "ld+json" in (t.get("type") or ""):
                continue
            t.decompose()
        text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
        raw_html = resp.text or ""

        # ---------------------------------------------------- entity matching
        orgnr_hit = bool(page_pattern(orgnr).search(text)) or bool(page_pattern(orgnr).search(raw_html))
        final_host = urlsplit(final_url).netloc.lower().removeprefix("www.")
        declared_host = urlsplit(url).netloc.lower().removeprefix("www.")
        name_in_text = any(v in text.casefold() for v in _name_variants(company_name))
        name_in_domain = _name_matches_domain(company_name, final_host)
        name_hit = name_in_text or name_in_domain
        parked = len(text) < 150 or bool(PARKED_RE.search(text[:3000]))
        same_host = final_host == declared_host or final_host.endswith("." + declared_host) or declared_host.endswith("." + final_host)
        res.checks.append({"check": "declared_in_registry", "passed": True,
                           "detail": f"URL declared by the entity in {declared_by}: {declared_url}"})
        res.checks.append({"check": "orgnr_on_page", "passed": orgnr_hit, "detail": f"org number {orgnr} printed on {final_url}"})
        res.checks.append({"check": "name_on_page", "passed": name_hit,
                           "detail": f"company name {company_name!r} " + ("found in page text" if name_in_text else
                                                                           "matches the domain name" if name_in_domain else
                                                                           "not found in page text or domain")})
        res.checks.append({"check": "same_host_after_redirect", "passed": same_host, "detail": f"{declared_host} -> {final_host}"})
        res.checks.append({"check": "not_placeholder", "passed": not parked, "detail": "page has real content (not parked/for sale)"})
        if orgnr_hit and not parked:
            match, confidence = "orgnr_on_page", "high"
        elif name_hit and not parked and same_host:
            match, confidence = "name_on_page", "medium"
        else:
            match, confidence = "unverified", "low"
        min_conf = ctx.opt("min_confidence", "medium")
        publish = CONFIDENCE_RANK[confidence] >= CONFIDENCE_RANK.get(min_conf, 1)
        res.status = "ok"
        res.data = {"final_url": final_url, "match": match, "confidence": confidence}

        facts = []
        F = lambda key, value, **kw: facts.append(fact(key, clean(value), source="website", url=final_url,  # noqa: E731
                                                       retrieved_at=ctx.retrieved_at, match=match, confidence=confidence,
                                                       source_date=(resp.headers.get("last-modified") or None), **kw))
        F("web.url", final_url, note="resolved from the registry-declared website")
        F("web.orgnr_on_page", orgnr_hit)
        title = (soup.title.get_text(" ", strip=True) if soup.title else "") or None
        if title:
            F("web.title", title[:200])
        meta = {}
        for m in soup.find_all("meta"):
            k = (m.get("property") or m.get("name") or "").lower()
            if k and m.get("content"):
                meta.setdefault(k, m["content"].strip())
        desc = meta.get("description") or meta.get("og:description") or meta.get("twitter:description")
        if desc:
            F("web.description", desc[:600])
        if meta.get("og:site_name"):
            F("web.site_name", meta["og:site_name"][:120])
        lang = (soup.html.get("lang") if soup.html else None) or meta.get("og:locale")
        if lang:
            F("web.language", lang[:10])
        if meta.get("og:image"):
            F("web.og_image", urljoin(final_url, meta["og:image"]))

        socials: dict[str, str] = {}
        hrefs = [urljoin(final_url, a.get("href")) for a in soup.find_all("a", href=True)]
        for href in hrefs + re.findall(r"https?://[^\s\"'<>]+", raw_html):
            for net, rx in SOCIAL.items():
                m = rx.match(href)
                if m and net not in socials:
                    socials[net] = m.group(0).rstrip("/")
        if socials:
            F("web.social", socials)
        emails = sorted({e.lower() for e in EMAIL_RE.findall(raw_html)
                         if not re.search(r"\.(png|jpe?g|gif|svg|webp|css|js)$", e, re.I)
                         and not e.lower().startswith(("noreply", "no-reply", "sentry", "example"))
                         and "wixpress" not in e and "@sentry" not in e})
        mailto = sorted({h[7:].split("?")[0].lower() for h in hrefs if h.lower().startswith("mailto:") and "@" in h})
        emails = sorted(set(mailto) | set(emails))[:5]
        if emails:
            F("web.emails", emails)
        phones = sorted({re.sub(r"[^\d+]", "", h[4:].split("?")[0]) for h in hrefs if h.lower().startswith("tel:")})
        phones = [p for p in phones if 6 <= len(re.sub(r"\D", "", p)) <= 15][:5]
        if phones:
            F("web.phones", phones)
        for org in _jsonld_orgs(soup)[:1]:
            logo = org.get("logo")
            if isinstance(logo, dict):
                logo = logo.get("url")
            F("web.jsonld_organization", {
                "name": org.get("name"), "legal_name": org.get("legalName"), "logo": logo,
                "same_as": org.get("sameAs") if isinstance(org.get("sameAs"), list) else ([org["sameAs"]] if org.get("sameAs") else None),
                "telephone": org.get("telephone"), "email": org.get("email"),
                "address": org.get("address") if isinstance(org.get("address"), dict) else None,
            })

        # ------------------------------------------------- optional about page
        about_text = None
        if ctx.opt("website_pages", 2) >= 2 and not ctx.tally.exhausted() and publish:
            for a in soup.find_all("a", href=True):
                label = a.get_text(" ", strip=True)
                if label and len(label) <= 40 and ABOUT_RE.search(label):
                    target = urljoin(final_url, a["href"]).split("#")[0]
                    if urlsplit(target).netloc.lower() == urlsplit(final_url).netloc.lower() and target != final_url:
                        if await ctx.http.allowed_by_robots(target, tally=ctx.tally):
                            ar = await ctx.http.get(target, tally=ctx.tally, max_bytes=1_000_000)
                            if ar.ok and ar.text:
                                asoup = BeautifulSoup(ar.text, "html.parser")
                                for t in asoup(["script", "style", "noscript", "svg", "nav", "footer", "header"]):
                                    t.decompose()
                                paras = [re.sub(r"\s+", " ", p.get_text(" ", strip=True)) for p in asoup.find_all("p")]
                                paras = [p for p in paras if len(p) > 80]
                                if paras:
                                    about_text = " ".join(paras)[:1200]
                                    facts.append(fact("web.about_text", about_text, source="website", url=ar.final_url,
                                                      retrieved_at=ctx.retrieved_at, match=match, confidence=confidence,
                                                      note="first paragraphs of the company's about page"))
                        break

        if publish:
            res.facts.extend(facts)
        else:
            res.rejected.extend({"key": f.key, "reason": f"page could not be tied to {orgnr} (no org number or company name on page); confidence={confidence}"}
                                for f in facts)
            res.notes.append("website-derived facts withheld: page does not identify the company")
    finally:
        res.requests = ctx.tally.requests - before
    return res
