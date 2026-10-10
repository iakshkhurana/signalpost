# Signalpost

An agent that takes a Norwegian organisation number (organisasjonsnummer) and
builds an evidence-backed company profile from permitted public sources. Every
fact carries the URL it came from, the date the source attaches to it, when we
fetched it, and an explicit statement of *why the record belongs to this
company*. Profiles are kept current: re-running the agent diffs the new facts
against the stored profile and records additions, changes and retractions.

Built for the Builderr "Signalpost" challenge.

## One command

```sh
# Linux / macOS
./run.sh companies.txt            # one organisation number per line
./run.sh 923609016 878703812      # or inline

# Windows
.\run.ps1 companies.txt

# or directly
pip install -r requirements.txt
python -m signalpost run --input companies.txt
```

Output: `profiles/<orgnr>.json` (one per company), `profiles/index.json`, and a
run report `runs/<timestamp>.json` with the budget actually used.

The defaults enforce the daily limits: at most **2000 outbound requests**,
**45 minutes**, and **$8 of declared LLM spend** (the LLM is off unless `--llm`
is passed). When a limit is hit the run stops cleanly, saves what it has, and
lists the companies it skipped.

Requirements: Python 3.11+, `httpx`, `beautifulsoup4`. Optional: `anthropic`
for `--llm`. No API keys are needed for the data sources.

## Sources (all permitted public sources, no scraping behind logins)

| Source | What it gives | Licence / terms | How we tie it to the company |
|---|---|---|---|
| Brreg **Enhetsregisteret** API (`data.brreg.no/enhetsregisteret/api/enheter/{orgnr}`) | legal name and name history, legal form, dates, addresses, NACE codes, sector, employees, declared website/phone, purpose, share capital, status flags (bankruptcy, liquidation), register memberships, parent | NLOD 2.0 (open government data) | looked up by org number; the record must echo the same number |
| Brreg **roles** (`/enheter/{orgnr}/roller`) | general manager (daglig leder), board chair and members, auditor, accountant, owner, partners | NLOD 2.0 | endpoint is addressed by org number; birth dates are **not** published |
| Brreg **sub-units** (`/underenheter?overordnetEnhet=`) | count and list of registered sub-units with location and employees | NLOD 2.0 | filtered on parent = org number and re-checked per unit |
| Brreg **Regnskapsregisteret** (`data.brreg.no/regnskapsregisteret/regnskap/{orgnr}`) | filed annual accounts: revenue, operating result, net result, assets, equity, debt, cash, audit info; company and group statements for the years the API returns | NLOD 2.0 | each filing's `virksomhet.organisasjonsnummer` must equal the org number; values are copied verbatim, never computed |
| **GLEIF** LEI API | LEI, legal name, addresses, legal form (ELF), registration status, parent reporting | CC0 | `entity.registeredAs` (digits) must equal the org number and the record must be Norwegian (RA000472 / NO) |
| **Wikidata** SPARQL | Wikipedia links, description, websites, inception, HQ, industries, employees, parent/owner, CEO, tickers | CC0 | item must carry property P2333 (Norwegian organisation number) = org number |
| **Wikipedia** REST summary | article description and lead paragraph (en / no) | CC BY-SA | article URL taken from the Wikidata item with P2333 = org number |
| **NAV job board** (`arbeidsplassen.nav.no/stillinger/api/search`) | active job postings: title, ad URL, published / expiry / application dates, location, engagement type, extent, positions | public state job board, robots.txt allows all | search by name; an ad is kept **only** if its employer name equals the registry name of the entity or one of its registered sub-units (fetched from Brreg) |
| **DiBK** central approval register (`sgregister.dibk.no/api/enterprises/{orgnr}`) | sentral godkjenning: valid-to date, approval areas (function / subject / class), insurance flags, certificate URL | public register, open JSON | addressed by org number; record must echo it; only queried for NACE 41-43 / 71 |
| **Kartverket** address API (`ws.geonorge.no/adresser`) | WGS84 coordinates, cadastral reference and map link for the registered business address | CC BY 4.0 | registry address looked up; postal code must match (labelled `address_lookup`, confidence medium) |
| **Company website** | home page title, description, headline, first paragraphs, language, JSON-LD organisation data; **contact** page (e-mails, phones); **about** page text; **careers** page (vacancy links, schema.org `JobPosting`); **news** via RSS/Atom feed or news page (dated items, `NewsArticle`); **products / services** page (headings, `Product`); social profiles | the company's own public pages; `robots.txt` honoured; home + at most 4 linked pages; identified user agent | URL is the one the company declared to Enhetsregisteret (or Wikidata). When none is declared, one guess `<name>.no` is tried and used **only** if the org number is printed on it (or the full registered name appears on a page whose domain is the name). Facts are published only if the site identifies the company: org number on any fetched page (**high**) or company name on the page / in the domain (**medium**). Otherwise the facts are withheld and listed under `sources[].rejected`. |

Not used on purpose: commercial aggregators (proff.no, 1881.no, Purehelp, etc.)
whose terms forbid automated access, search engines, social networks' own
pages, and anything requiring login.

## Profile format

```jsonc
{
  "orgnr": "923609016",
  "name": "EQUINOR ASA",
  "last_run": "2026-10-05T05:06:17Z", "run_count": 3,
  "entity_status": "ok",                      // ok | not_found | deleted
  "matching": {
    "overall": "high",                        // high | medium | none
    "checks": [ { "source": "gleif", "check": "lei_registered_as", "passed": true, "detail": "..." }, ... ],
    "summary": [ "brreg_enheter: looked up by org number; record echoes the number", ... ]
  },
  "facts": [
    {
      "key": "finance.2024.konsern.revenue",
      "value": { "amount": 103774000000.0, "currency": "USD", "period_from": "2024-01-01", "period_to": "2024-12-31" },
      "confidence": "high",
      "status": "active",                     // active | retracted
      "first_seen": "...", "last_seen": "...",
      "history": [ { "value": ..., "until": "..." } ],
      "evidence": [ {
        "source": "brreg_regnskap",
        "url": "https://data.brreg.no/regnskapsregisteret/regnskap/923609016",
        "source_date": "2024-12-31",          // date the source attaches to the fact
        "retrieved_at": "2026-10-05T05:06:17Z",
        "match": "orgnr_exact",               // orgnr_exact | registry_declared | orgnr_on_page | name_on_page
        "note": "Regnskapsregisteret filing id=5667199 journalnr=2025428073; value copied verbatim"
      } ]
    }
  ],
  "sources": [ { "source": "website", "status": "ok", "url": "...", "requests": 3, "checks": [...], "rejected": [...] } ],
  "last_run_changes": [ { "at": "...", "key": "employees", "type": "changed", "old": 21272, "new": 21300 } ],
  "changes": [ ... ],                         // full change log (last 500)
  "explanation": { "mode": "template", "text": "...",     // or mode "llm" with summary/highlights/caveats
                   "sections": { "what_it_does": [...], "key_numbers": [...], "changes": [...], "unknown": [...], "sources": [...] } },
  "stats": { "active_facts": 170, "retracted_facts": 0, "stale_facts": 0, "requests": 9, "seconds": 4.0 }
}
```

Fact keys are stable, dotted names: `name`, `org_form`, `founded`,
`address.business`, `industry.nace1`, `employees`, `website`, `status`,
`registers`, `capital.share`, `roles.ceo`, `roles.board_chair`,
`roles.board_members`, `subunits.count`, `finance.<year>.<selskap|konsern>.<metric>`,
`lei`, `wikipedia.en.summary`, `web.description`, `web.about_text`, `web.social`,
`web.emails`, `web.products`, `web.careers.listings`, `news.item.<id>`,
`jobs.active_count`, `jobs.posting.<uuid>`, `jobs.web_posting.<id>`,
`location.business.geo`, `approvals.dibk_central_approval`, ... Each key maps to
one **information type** (`signalpost/facttypes.py`): identity, industry,
address, location, contacts, social, people, employees, subunits, finance,
capital, ownership, jobs, news, products, web, approvals, lei, wikipedia. Use
`python -m signalpost show <orgnr>` for a table or the web viewer below.

## Web viewer (desktop and mobile)

`index.html` at the repository root is a dependency-free viewer over
`profiles/`: search by name or org number, filter by information type and
match level, open a profile (what the company does, key numbers, what changed,
what remains unknown, every fact grouped by type with its evidence URL, source
date, fetch time and match type), and compare up to four companies side by
side with a link to the evidence behind each cell.

```sh
python -m signalpost serve          # http://127.0.0.1:8765/
```

It is plain HTML + JS over the JSON files, so it also works on any static
host (GitHub Pages from the repository root).

## Keeping profiles current

* `python -m signalpost run ...` on an already-profiled company merges the new
  facts into the stored profile: new keys are **added**, different values are
  **changed** (the old value moves to `history`), keys that a *successfully
  fetched* source no longer reports are **retracted** (kept with
  `status: retracted`), and keys whose source errored or ran out of budget are
  kept and marked `stale` rather than deleted.
* `--sources nav_jobs,website` re-fetches only the named sources; the other
  sources' facts are kept from the stored profile without being marked stale
  (cheap daily re-checks of the fast-moving sources).
* `python -m signalpost refresh` asks Brreg's update feed
  (`/oppdateringer/enheter`) which stored companies changed since the last run,
  adds profiles older than `--stale-days` (default 7, because accounts, LEI and
  Wikidata have no change feed), and re-runs only those. `--all` re-runs everything.

## Accuracy safeguards

* Financial values come **only** from Regnskapsregisteret filings and are copied
  verbatim with the filing id and journal number in the evidence. Nothing is
  derived, scaled or converted.
* Every registry response is checked to echo the requested org number; GLEIF and
  Wikidata are matched on exact identifiers; website facts need the org number
  or company name on the page or domain.
* Sub-unit numbers (underenheter) are recognised and labelled, with a pointer to
  the parent entity.
* Personal data is minimised: role holders are published by name and role only
  (no birth dates), as they appear in the public register.
* `python -m signalpost validate` checks every stored profile: valid check digit,
  evidence with URL and date on every fact, no financial fact from a non-filing
  source, no low-confidence website fact published.

## Explanations

Each profile carries a deterministic, template-based explanation in five
sections, each sentence cited with its source: **what the company does**
(registry form and purpose, industry, website and Wikipedia descriptions,
address, products, leadership, ownership), **key numbers** (employees, latest
accounts with year-over-year change, share capital, open jobs, news, LEI,
approvals), **what changed** in this run, **what remains unknown** (information
types looked for and not found, with the reason per source) and **how each
source was tied** to the company. With `--llm` and `ANTHROPIC_API_KEY` set, Claude
(`claude-opus-5-5` by default, `--model` to change) writes a richer
summary/highlights/caveats from the same facts. Its output is accepted only if
every number of four or more digits it mentions exists in the input facts;
otherwise the template is used. The LLM never adds facts to the profile.

## Model / API details and expected costs

| Item | Detail |
|---|---|
| Data APIs | Brreg (Enhetsregisteret, Regnskapsregisteret), GLEIF, Wikidata: free, no key, no declared cost |
| Website fetches | robots.txt + home + ≤ 4 linked pages (contact, about, careers, news/RSS, products) per company; the registry-declared site, or one `<name>.no` guess when none is declared |
| Other per-company lookups | NAV job search (1, paced 3 s apart), Kartverket geocoding (1), DiBK (1, construction / engineering only), Wikipedia (≤ 2, only when Wikidata links an article) |
| Requests per company | measured over 1000 companies: mean 11.8, median 11, p95 17, max 20; hard cap `--per-company-requests 25` |
| 100 companies / day | ≈ 1200 outbound requests (cap 2000), ≈ 6 minutes at `--concurrency 8` (1000 companies took 35 min at `--concurrency 14`) |
| LLM (optional) | `claude-opus-5-5` via the Anthropic API, structured JSON output, low effort, server-side refusal fallback enabled; ≈ 3k input + 400 output tokens per company → ≈ $0.02 per company, ≈ $2 per 100 companies; hard cap `--max-cost 8` |
| Declared daily external API cost | **$0.00** without `--llm`; **≈ $2 (≤ $8 cap)** with `--llm` |

## Measured coverage (the 1000 submitted profiles)

| | |
|---|---|
| Profiles | 1000, `validate` reports 0 problems |
| Facts per profile | mean 83.6 (min 17, max 182) |
| Brreg entity / roles / sub-units | 100 % |
| Filed annual accounts | 88 % (10.8 % have no accounts in the open API, 1.2 % the API answers HTTP 500) |
| Map coordinates of the business address (Kartverket) | 86.8 % |
| Company website | 33 % declare a site; with the `<name>.no` guess 30.5 % of all companies yielded a verified site (54 of them guessed, all with the org number printed on the site). Of the sites fetched, 32 % print the org number (high), 44 % match by name (medium), 23 % withheld as unverifiable |
| Contact details (website, phone, e-mail) | 68.3 % |
| Social media profiles | 19.5 % |
| Job postings (NAV board, careers page) | 10.5 % of companies (188 postings); NAV's board rate-limited the bulk run after the first requests (HTTP 429), so most of the 1000 profiles still lack NAV data; requests to it are now paced at one per 3 s |
| News (RSS/Atom feed, news page) | 9.8 % (418 items) |
| Products / services | 5.0 % |
| DiBK central approval | 6.3 % (45 % of the construction / engineering companies queried) |
| GLEIF LEI | 20.9 % |
| Wikidata / Wikipedia | 9.5 % (Wikipedia summaries for 7.5 %) |
| Entity-match verdict | high 91 %, medium 9 % (medium = website facts matched by name only) |

`python -m signalpost stats` prints the per-type table (companies with at least
one fact of the type, and the number of facts).

## Commands

```
python -m signalpost run <orgnr ...> | --input FILE   profile companies (see --help for budgets, --llm, --cache-dir)
python -m signalpost refresh [--all] [--stale-days N]  re-run changed / stale profiles via the Brreg update feed
python -m signalpost discover --count 1000            sample a varied set of companies from Enhetsregisteret
python -m signalpost show <orgnr> [--json] [--all]    print explanation and fact table
python -m signalpost validate                         integrity checks over the store
python -m signalpost stats                            coverage statistics
python -m signalpost export --out-file profiles.jsonl single-file export
python -m signalpost serve                            web viewer at http://127.0.0.1:8765/
python -m signalpost index                            rebuild profiles/index.json
python -m unittest discover -s tests                  unit tests (offline)
```

## Repository layout

```
signalpost/
  cli.py          commands
  pipeline.py     per-company orchestration: fetch -> match -> merge -> explain -> save
  http.py         budgeted async client (request/time/cost caps, retries, robots.txt, dev cache)
  models.py       Fact / Evidence / SourceResult
  merge.py        update logic (added / changed / retracted / stale)
  match.py        cross-source entity-matching summary
  explain.py      template explanation      llm.py  optional Claude explanation
  discover.py     sampling companies from the registry
  facttypes.py    fact key -> information type
  sources/        brreg.py  regnskap.py  gleif.py  wikidata.py  wikipedia.py  website.py
                  nav_jobs.py  dibk.py  geonorge.py
index.html                web viewer over profiles/ (python -m signalpost serve)
data/companies-1000.txt   the sample used for the submitted profiles
profiles/                 1000+ generated profiles + index.json
tests/                    unit tests
```
