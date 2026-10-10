"""Company website: the URL the company itself declared to Enhetsregisteret
(or, when none is declared, a single guessed domain built from the registered
name that must then prove itself by printing the organisation number).

We fetch robots.txt, the home page and up to a few linked pages of known
kinds (contact, about, careers, news or its RSS feed, products). Facts
extracted from the pages are only published when the site ties back to the
company: the org number printed on one of the pages (high) or the company
name on the page / in the domain (medium). Otherwise the page-derived facts
are rejected and listed as such.
"""
from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
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
PARKED_RE = re.compile(r"(domain (is )?for sale|parked (domain|page)|dette domenet|domenet er (parkert|til salgs)|buy this domain|"
                       r"under construction|kommer snart|coming soon|website is (temporarily )?unavailable|account suspended)", re.I)
LEGAL_SUFFIX_RE = re.compile(r"\b(asa|as|a/s|ans|da|enk|nuf|sa|ba|ks|iks|fkf|kf|stiftelsen|stiftelse|sf)\b\.?", re.I)

# Kinds of linked pages we look for, in the order we spend requests on them.
PAGE_KINDS: list[tuple[str, re.Pattern[str], re.Pattern[str]]] = [
    ("contact", re.compile(r"\b(kontakt(?: oss)?|contact(?: us)?|kontaktinformasjon|ta kontakt)\b", re.I),
     re.compile(r"/(kontakt|contact)", re.I)),
    ("about", re.compile(r"\b(om oss|om selskapet|om bedriften|om firmaet|about(?: us)?|who we are|vår historie|om [a-zæøå0-9\-]+)\b", re.I),
     re.compile(r"/(om-oss|om_oss|about|om-selskapet|om)(/|$|\.)", re.I)),
    ("careers", re.compile(r"\b(karriere|ledige stillinger|jobb hos oss|jobb i [a-zæøå]+|stillinger|jobb|career[s]?|jobs?|vacancies|work (with|for) us|join us|bli med)\b", re.I),
     re.compile(r"/(karriere|jobb|stillinger|ledige-stillinger|career[s]?|jobs?|vacancies)", re.I)),
    ("news", re.compile(r"\b(nyheter|aktuelt|presse|pressemeldinger|news|blogg?|press(?: releases?)?|artikler|siste nytt)\b", re.I),
     re.compile(r"/(nyheter|aktuelt|news|blog|blogg|presse|press)", re.I)),
    ("products", re.compile(r"\b(produkter|tjenester|våre tjenester|products?|services|løsninger|solutions|hva vi gjør|what we do|sortiment|våre produkter)\b", re.I),
     re.compile(r"/(produkter|tjenester|products?|services|losninger|solutions)", re.I)),
]
_SKIP_HREF = re.compile(r"\.(pdf|jpe?g|png|gif|svg|webp|zip|docx?|xlsx?|pptx?|mp4|mp3)(\?|$)|^(mailto|tel|javascript):", re.I)
GUESS_FORMS = {"AS", "ASA", "SA", "BA", "NUF", "STI", "FLI", "SAM", "BRL", "AAFY", "ANNA"}
_GENERIC_WORDS = {"norge", "norway", "nordic", "norsk", "norske", "holding", "invest", "eiendom", "gruppen", "group",
                  "partner", "partners", "consulting", "service", "services", "bygg", "transport", "international"}


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


def _name_tokens(name: str | None) -> list[str]:
    stripped = LEGAL_SUFFIX_RE.sub(" ", (name or "").casefold()).translate(_FOLD)
    tokens = [re.sub(r"[^a-z0-9]", "", t) for t in stripped.split()]
    return [t for t in tokens if t]


def _name_matches_domain(name: str | None, host: str) -> bool:
    """'ADM AS' vs adm.no, '4SUBSEA AS' vs 4subsea.com, 'Bilkollektivet SA' vs bilkollektivet.no."""
    if not name:
        return False
    label = host.lower().removeprefix("www.").split(".")[0]
    label = re.sub(r"[^a-z0-9]", "", label)
    tokens = _name_tokens(name)
    compact = "".join(tokens)
    if len(label) < 3 or len(compact) < 3:
        return False
    if compact == label:
        return True                                   # 'bilkollektivet sa' vs bilkollektivet.no
    if tokens and len(tokens[0]) >= 4 and tokens[0] == label:
        return True                                   # 'kjell & co norway as' vs kjell.com (brand = first word)
    return len(label) >= 6 and compact.startswith(label)   # 'havila subsea crewing' vs havilasubsea.no


def guess_domains(name: str | None, org_form: str | None) -> list[str]:
    """One candidate home page built from the registered name, for companies
    that declared no website. Only for forms that usually have a site, and
    only when the name is distinctive enough that a hit would be meaningful."""
    if not name or (org_form or "").upper() not in GUESS_FORMS:
        return []
    tokens = _name_tokens(name)
    if not tokens or len(tokens) > 4:
        return []
    compact = "".join(tokens)
    if len(compact) < 5 or len(compact) > 30 or compact.isdigit():
        return []
    if len(tokens) == 1 and tokens[0] in _GENERIC_WORDS:
        return []
    return [f"https://{compact}.no"]


def _jsonld_items(soup: BeautifulSoup) -> list[dict]:
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
                if isinstance(item.get("itemListElement"), list):
                    stack.extend(x.get("item", x) for x in item["itemListElement"] if isinstance(x, dict))
                if item.get("@type"):
                    found.append(item)
    return found


def _types(item: dict) -> list[str]:
    t = item.get("@type")
    return [x for x in (t if isinstance(t, list) else [t]) if isinstance(x, str)]


def _jsonld_orgs(soup: BeautifulSoup) -> list[dict]:
    return [i for i in _jsonld_items(soup)
            if any(x.endswith(("Organization", "Corporation", "LocalBusiness")) for x in _types(i))]


def _text(soup: BeautifulSoup) -> str:
    return re.sub(r"\s+", " ", soup.get_text(" ", strip=True))


def _strip(soup: BeautifulSoup, chrome: bool = False) -> None:
    for t in soup(["script", "style", "noscript", "svg"] + (["nav", "footer", "header", "aside", "form"] if chrome else [])):
        if t.name == "script" and "ld+json" in (t.get("type") or ""):
            continue
        t.decompose()


def _paragraphs(html: str, *, min_len: int = 80, limit: int = 1200) -> str | None:
    s = BeautifulSoup(html, "html.parser")
    _strip(s, chrome=True)
    paras = [re.sub(r"\s+", " ", p.get_text(" ", strip=True)) for p in s.find_all(["p", "li"])]
    paras = [p for p in paras if len(p) >= min_len]
    return " ".join(paras)[:limit] if paras else None


def _headings(soup: BeautifulSoup, *, limit: int = 15) -> list[str]:
    out: list[str] = []
    for h in soup.find_all(["h2", "h3"]):
        t = re.sub(r"\s+", " ", h.get_text(" ", strip=True))
        if 3 <= len(t) <= 80 and t not in out and not re.search(r"\b(cookie|personvern|privacy|meny|menu|søk|search)\b", t, re.I):
            out.append(t)
        if len(out) >= limit:
            break
    return out


def _hid(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8", "replace")).hexdigest()[:12]


def _date(s: str | None) -> str | None:
    if not s:
        return None
    s = s.strip()
    try:
        return parsedate_to_datetime(s).date().isoformat()
    except Exception:
        pass
    m = re.match(r"(\d{4}-\d{2}-\d{2})", s)
    if m:
        return m.group(1)
    for fmt in ("%d.%m.%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s[:10], fmt).date().isoformat()
        except Exception:
            continue
    return None


def parse_feed(xml_text: str, base_url: str, *, limit: int = 10) -> list[dict]:
    """RSS 2.0 / Atom -> [{title, url, date, summary}]."""
    try:
        root = ET.fromstring(xml_text.strip().encode("utf-8", "replace"))
    except ET.ParseError:
        return []
    items: list[dict] = []

    def tag(e):  # strip namespaces
        return e.tag.split("}")[-1].lower()

    for e in root.iter():
        if tag(e) not in ("item", "entry"):
            continue
        d: dict = {}
        for c in e:
            t = tag(c)
            if t == "title":
                d["title"] = re.sub(r"\s+", " ", (c.text or "").strip())
            elif t == "link":
                d["url"] = (c.get("href") or c.text or "").strip() if (c.get("rel") in (None, "alternate")) or not d.get("url") else d.get("url")
            elif t in ("pubdate", "published", "updated", "date") and not d.get("date"):
                d["date"] = _date(c.text)
            elif t in ("description", "summary", "content") and not d.get("summary"):
                summ = BeautifulSoup(c.text or "", "html.parser").get_text(" ", strip=True)
                d["summary"] = re.sub(r"\s+", " ", summ)[:300] or None
        if d.get("title") and d.get("url"):
            d["url"] = urljoin(base_url, d["url"])
            items.append(d)
        if len(items) >= limit:
            break
    return items


def classify_links(soup: BeautifulSoup, final_url: str) -> dict[str, str]:
    """Pick one internal URL per page kind from the anchors on a page."""
    host = urlsplit(final_url).netloc.lower().removeprefix("www.")
    picks: dict[str, str] = {}
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or _SKIP_HREF.search(href):
            continue
        target = urljoin(final_url, href).split("#")[0]
        t_host = urlsplit(target).netloc.lower().removeprefix("www.")
        if t_host != host or target.rstrip("/") == final_url.rstrip("/"):
            continue
        label = re.sub(r"\s+", " ", a.get_text(" ", strip=True))
        if len(label) > 40:
            continue
        path = urlsplit(target).path
        for kind, label_rx, href_rx in PAGE_KINDS:
            if kind in picks:
                continue
            if (label and label_rx.search(label)) or href_rx.search(path):
                picks[kind] = target
                break
    return picks


def feed_links(soup: BeautifulSoup, final_url: str) -> list[str]:
    out = []
    for l in soup.find_all("link", href=True):
        rel = " ".join(l.get("rel") or []).lower()
        typ = (l.get("type") or "").lower()
        if "alternate" in rel and ("rss" in typ or "atom" in typ):
            out.append(urljoin(final_url, l["href"]))
    return out


def _careers_listings(soup: BeautifulSoup, page_url: str, *, limit: int = 15) -> list[dict]:
    rx = re.compile(r"(stilling|jobb|job|career|karriere|ledig|position|vacanc|apply|søk)", re.I)
    out: list[dict] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        title = re.sub(r"\s+", " ", a.get_text(" ", strip=True))
        href = urljoin(page_url, a["href"]).split("#")[0]
        if not (6 <= len(title) <= 90) or href in seen or href.rstrip("/") == page_url.rstrip("/"):
            continue
        if not rx.search(href) and not rx.search(title):
            continue
        if re.search(r"^(ledige stillinger|karriere|jobb hos oss|jobs?|careers?|se alle|les mer|read more|søk her|apply)$", title, re.I):
            continue
        if re.search(r"^(norwegian|english|norsk|engelsk|no|en|nb|nn|sv|da|de)", title, re.I) or re.search(r"^/(no|en|nb|nn|sv|da|de)/?[^/]*$", urlsplit(href).path, re.I):
            continue   # language switchers
        seen.add(href)
        out.append({"title": title, "url": href})
        if len(out) >= limit:
            break
    return out


def _news_headlines(soup: BeautifulSoup, page_url: str, *, limit: int = 10) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    host = urlsplit(page_url).netloc.lower().removeprefix("www.")
    base_path = urlsplit(page_url).path.rstrip("/")
    newsish = re.compile(r"/(news|nyheter|aktuelt|artikler|artikkel|blog|blogg|press|presse|innlegg|post)", re.I)
    for t in soup(["nav", "footer", "header", "aside"]):
        t.decompose()
    for a in soup.find_all("a", href=True):
        title = re.sub(r"\s+", " ", a.get_text(" ", strip=True))
        href = urljoin(page_url, a["href"]).split("#")[0]
        if urlsplit(href).netloc.lower().removeprefix("www.") != host or href in seen:
            continue
        if not (20 <= len(title) <= 140) or href.rstrip("/") == page_url.rstrip("/"):
            continue
        path = urlsplit(href).path
        if not ((base_path and path.startswith(base_path + "/")) or newsish.search(path)):
            continue
        seen.add(href)
        out.append({"title": title, "url": href})
        if len(out) >= limit:
            break
    return out


async def fetch_website(ctx: Ctx, orgnr: str, declared_url: str | None, company_name: str | None,
                        *, declared_by: str = "brreg_enheter", org_form: str | None = None) -> SourceResult:
    res = SourceResult(source="website", url=declared_url or "", status="skipped", retrieved_at=ctx.retrieved_at)
    candidates: list[tuple[str, str]] = []
    url = normalize_url(declared_url)
    if url:
        candidates.append((url, declared_by))
    elif ctx.opt("guess_website", True):
        candidates.extend((g, "guessed_from_name") for g in guess_domains(company_name, org_form))
    if not candidates:
        res.notes.append("no usable website declared" + ("" if url or not company_name else "; no domain guess attempted"))
        return res
    before = ctx.tally.requests
    try:
        for url, declared_by in candidates:
            res.url = url
            guessed = declared_by == "guessed_from_name"
            if not await ctx.http.allowed_by_robots(url, tally=ctx.tally):
                res.status = "blocked"
                res.notes.append("robots.txt disallows fetching this page for our user agent")
                continue
            if guessed and ctx.http.unreachable(url):
                res.status = "not_found"
                res.notes.append(f"guessed domain {urlsplit(url).netloc} does not respond")
                continue
            resp = await ctx.http.get(url, tally=ctx.tally, max_bytes=1_500_000, retries=0 if guessed else 2)
            if not guessed and (resp.error or (resp.status and resp.status >= 400)) and url.startswith("https://"):
                alt = "http://" + url[len("https://"):]
                resp2 = await ctx.http.get(alt, tally=ctx.tally, max_bytes=1_500_000)
                if resp2.ok:
                    resp = resp2
            res.http_status = resp.status
            if resp.error:
                res.status = "not_found" if guessed else "error"
                res.notes.append(resp.error if not guessed else f"guessed domain {urlsplit(url).netloc} does not respond")
                continue
            if not resp.ok:
                res.status = "not_found" if guessed else "error"
                res.notes.append(f"HTTP {resp.status}")
                continue
            ctype = (resp.headers.get("content-type") or "").lower()
            if ctype and "html" not in ctype and "xml" not in ctype and "text" not in ctype:
                res.status = "error"
                res.notes.append(f"not an HTML page ({ctype})")
                continue
            await _extract_site(ctx, res, orgnr, url, declared_url, declared_by, company_name, resp)
            if res.status == "ok":
                break
    finally:
        res.requests = ctx.tally.requests - before
    return res


async def _extract_site(ctx: Ctx, res: SourceResult, orgnr: str, url: str, declared_url: str | None,
                        declared_by: str, company_name: str | None, resp) -> None:
    guessed = declared_by == "guessed_from_name"
    final_url = resp.final_url
    raw_html = resp.text or ""
    soup = BeautifulSoup(raw_html, "html.parser")
    _strip(soup)
    text = _text(soup)
    rx = page_pattern(orgnr)

    # ---------------------------------------------------------------- entity matching
    orgnr_hit = bool(rx.search(text)) or bool(rx.search(raw_html))
    orgnr_page = final_url if orgnr_hit else None
    final_host = urlsplit(final_url).netloc.lower().removeprefix("www.")
    declared_host = urlsplit(url).netloc.lower().removeprefix("www.")
    name_in_text = any(v in text.casefold() for v in _name_variants(company_name))
    full_name_in_text = bool(company_name) and company_name.casefold() in text.casefold()
    name_in_domain = _name_matches_domain(company_name, final_host)
    name_hit = name_in_text or name_in_domain
    parked = len(text) < 150 or bool(PARKED_RE.search(text[:3000]))
    same_host = final_host == declared_host or final_host.endswith("." + declared_host) or declared_host.endswith("." + final_host)
    res.checks = []
    if guessed:
        res.checks.append({"check": "guessed_from_name", "passed": True,
                           "detail": f"no website declared in the registry; {url} was derived from the registered name and must identify the company itself"})
    else:
        res.checks.append({"check": "declared_in_registry", "passed": True,
                           "detail": f"URL declared by the entity in {declared_by}: {declared_url}"})
    name_check = {"check": "name_on_page", "passed": name_hit,
                  "detail": f"company name {company_name!r} " + ("found in page text" if name_in_text else
                                                                  "matches the domain name" if name_in_domain else
                                                                  "not found in page text or domain")}
    host_check = {"check": "same_host_after_redirect", "passed": same_host, "detail": f"{declared_host} -> {final_host}"}
    parked_check = {"check": "not_placeholder", "passed": not parked, "detail": "page has real content (not parked/for sale)"}

    def decide() -> tuple[str, str]:
        if orgnr_hit and not parked:
            return "orgnr_on_page", "high"
        if parked or not same_host:
            return "unverified", "low"
        if guessed:
            return ("name_on_page", "medium") if (full_name_in_text and name_in_domain) else ("unverified", "low")
        return ("name_on_page", "medium") if name_hit else ("unverified", "low")

    match, confidence = decide()
    min_conf = ctx.opt("min_confidence", "medium")
    publish = CONFIDENCE_RANK[confidence] >= CONFIDENCE_RANK.get(min_conf, 1)
    if guessed and not publish:
        res.status = "not_found"
        res.checks.append({"check": "orgnr_on_page", "passed": False, "detail": f"org number {orgnr} not printed on {final_url}; guessed site discarded"})
        res.checks.append(name_check)
        res.notes.append(f"guessed site {final_url} does not identify the company; nothing taken from it")
        return
    res.status = "ok"

    facts = []
    site_date = resp.headers.get("last-modified") or None

    def F(key, value, *, page=None, date=None, **kw):
        facts.append(fact(key, clean(value), source="website", url=page or final_url, retrieved_at=ctx.retrieved_at,
                          match=match, confidence=confidence, source_date=_date(date) if date else _date(site_date), **kw))

    F("web.url", final_url, note="resolved from the registry-declared website" if not guessed else "home page found from the registered name; org number printed on the site")
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
    home_text = _paragraphs(raw_html, limit=700)
    if home_text:
        F("web.home_text", home_text, note="first substantive paragraphs of the home page")
    h1 = soup.find("h1")
    if h1:
        t = re.sub(r"\s+", " ", h1.get_text(" ", strip=True))
        if 3 <= len(t) <= 160:
            F("web.headline", t)

    socials: dict[str, str] = {}
    emails: set[str] = set()
    phones: set[str] = set()

    def harvest_contacts(s: BeautifulSoup, html: str) -> None:
        hrefs = [urljoin(final_url, a.get("href")) for a in s.find_all("a", href=True)]
        for href in hrefs + re.findall(r"https?://[^\s\"'<>]+", html):
            for net, srx in SOCIAL.items():
                m = srx.match(href)
                if m and net not in socials:
                    socials[net] = m.group(0).rstrip("/")
        emails.update(e.lower() for e in EMAIL_RE.findall(html)
                      if not re.search(r"\.(png|jpe?g|gif|svg|webp|css|js)$", e, re.I)
                      and not e.lower().startswith(("noreply", "no-reply", "sentry", "example"))
                      and "wixpress" not in e and "@sentry" not in e and "sentry.io" not in e)
        emails.update(h[7:].split("?")[0].strip().lower() for h in hrefs if h.lower().startswith("mailto:") and "@" in h)
        for h in hrefs:
            if h.lower().startswith("tel:"):
                p = re.sub(r"[^\d+]", "", h[4:].split("?")[0])
                if 6 <= len(re.sub(r"\D", "", p)) <= 15:
                    phones.add(p)

    harvest_contacts(soup, raw_html)
    jsonld_seen = 0
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
        jsonld_seen += 1

    # ------------------------------------------------------------- linked pages
    pages_budget = int(ctx.opt("website_pages", 5)) - 1
    picks = classify_links(soup, final_url)
    feeds = feed_links(soup, final_url)
    fetched_pages: dict[str, str] = {}
    products: list[str] = []
    news_items: list[dict] = []
    job_items: list[dict] = []
    contact_page: dict | None = None

    async def get_page(target: str, *, max_bytes: int = 1_000_000):
        if ctx.tally.exhausted():
            return None
        if not await ctx.http.allowed_by_robots(target, tally=ctx.tally):
            return None
        r = await ctx.http.get(target, tally=ctx.tally, max_bytes=max_bytes, retries=1)
        return r if r.ok and r.text else None

    # an unverified home page only earns the contact/about pages, where the org number is usually printed
    order = ["contact", "about", "careers", "news", "products"] if publish else ["contact", "about"]
    for kind in order:
        if pages_budget <= 0:
            break
        if kind == "news" and feeds:
            r = await get_page(feeds[0], max_bytes=600_000)
            pages_budget -= 1
            if r:
                items = parse_feed(r.text, r.final_url)
                if items:
                    news_items = [{**i, "feed": r.final_url} for i in items]
                    fetched_pages["feed"] = r.final_url
                    continue   # feed worked; skip the HTML news page
        target = picks.get(kind)
        if not target:
            continue
        r = await get_page(target)
        pages_budget -= 1
        if not r:
            continue
        page_url = r.final_url
        fetched_pages[kind] = page_url
        psoup = BeautifulSoup(r.text, "html.parser")
        _strip(psoup)
        ptext = _text(psoup)
        if not orgnr_hit and (rx.search(ptext) or rx.search(r.text)):
            orgnr_hit, orgnr_page = True, page_url
        harvest_contacts(psoup, r.text)
        for item in _jsonld_items(psoup):
            types = _types(item)
            if any(t == "JobPosting" for t in types):
                job_items.append({"title": item.get("title"), "url": item.get("url") or page_url, "date_posted": _date(item.get("datePosted")),
                                  "valid_through": _date(item.get("validThrough")), "employment_type": item.get("employmentType"),
                                  "location": ((item.get("jobLocation") or {}).get("address") or {}).get("addressLocality") if isinstance(item.get("jobLocation"), dict) else None,
                                  "page": page_url})
            elif any(t in ("NewsArticle", "BlogPosting", "Article") for t in types) and item.get("headline"):
                news_items.append({"title": item.get("headline"), "url": item.get("url") or (item.get("mainEntityOfPage") if isinstance(item.get("mainEntityOfPage"), str) else page_url),
                                   "date": _date(item.get("datePublished")), "summary": (item.get("description") or "")[:300] or None, "feed": None})
            elif any(t == "Product" for t in types) and item.get("name") and item["name"] not in products:
                products.append(item["name"][:80])
        if kind == "about":
            about = _paragraphs(r.text)
            if about:
                F("web.about_text", about, page=page_url, date=r.headers.get("last-modified"), note="first paragraphs of the company's about page")
        elif kind == "contact":
            contact_page = {"url": page_url}
        elif kind == "careers":
            listings = _careers_listings(psoup, page_url)
            F("web.careers_url", page_url, page=page_url, date=r.headers.get("last-modified"))
            if listings and not job_items:
                F("web.careers.listings", listings, page=page_url, date=r.headers.get("last-modified"),
                  note="links on the careers page that look like individual vacancies; titles as printed")
        elif kind == "news":
            if not news_items:
                heads = _news_headlines(psoup, page_url)
                if heads:
                    F("web.news.headlines", heads, page=page_url, date=r.headers.get("last-modified"),
                      note="latest headlines linked from the news page (no dates given on the page)")
        elif kind == "products":
            for h in _headings(psoup):
                if h not in products:
                    products.append(h)
            F("web.products_url", page_url, page=page_url, date=r.headers.get("last-modified"))

    if products:
        F("web.products", products[:15], page=fetched_pages.get("products"), date=None,
          note="product / service names as headed on the products page")
    for it in news_items[:10]:
        if it.get("title") and it.get("url"):
            F(f"news.item.{_hid(it['url'])}", {"title": it["title"][:200], "url": it["url"], "date": it.get("date"), "summary": it.get("summary")},
              page=it["url"], date=it.get("date"), note="from the site's RSS/Atom feed" if it.get("feed") else "from structured data on the news page")
    for j in job_items[:15]:
        if j.get("title"):
            F(f"jobs.web_posting.{_hid(j['url'] + (j['title'] or ''))}", {k: v for k, v in j.items() if k != "page"},
              page=j["url"], date=j.get("date_posted"), note="schema.org JobPosting on the company's own careers page")
    if job_items:
        F("jobs.web_count", len(job_items), page=fetched_pages.get("careers"), date=None)
    if contact_page:
        F("web.contact_page", contact_page, page=contact_page["url"], date=None)
    if socials:
        F("web.social", socials)
    if emails:
        F("web.emails", sorted(emails)[:5], note="e-mail addresses printed on the site (home/contact pages)")
    if phones:
        F("web.phones", sorted(phones)[:5], note="tel: links on the site (home/contact pages)")
    if fetched_pages:
        F("web.pages_checked", {"home": final_url, **fetched_pages}, date=None)

    # a later page printed the org number -> upgrade every website fact to high
    if orgnr_hit and not parked and match != "orgnr_on_page":
        match, confidence = "orgnr_on_page", "high"
        for f in facts:
            for e in f.evidence:
                e.match, e.confidence = match, confidence
        publish = True
    F("web.orgnr_on_page", orgnr_hit, page=orgnr_page, date=None)
    res.checks.append({"check": "orgnr_on_page", "passed": orgnr_hit,
                       "detail": f"org number {orgnr} printed on {orgnr_page}" if orgnr_hit else f"org number {orgnr} not found on the pages checked"})
    res.checks.extend([name_check, host_check, parked_check])
    res.data = {"final_url": final_url, "match": match, "confidence": confidence, "pages": fetched_pages}
    if publish:
        res.facts.extend(facts)
    else:
        res.rejected.extend({"key": f.key, "reason": f"page could not be tied to {orgnr} (no org number or company name on page); confidence={confidence}"}
                            for f in facts)
        res.notes.append("website-derived facts withheld: page does not identify the company")
