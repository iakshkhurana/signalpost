# Signalpost — submission sheet

Entry for **Signalpost: Build An Agent That Finds Company Information** (Builderr / Unstop).
Send this file, the repository link and the commit hash to submit@builderr.ai.

| Item | Value |
|---|---|
| Repository | https://github.com/iakshkhurana/signalpost |
| Exact commit hash | `d5cb070546c10786f1faa8ceba50ef7c9e2f4ffa` (code and the 1000 profiles; the commit after it only fills in this sheet) |
| One command to run | `./run.sh companies.txt` (or `python -m signalpost run --input companies.txt`) |
| Input format | text file, one 9-digit Norwegian organisation number per line (`#` comments allowed) |
| Output | `profiles/<orgnr>.json` per company, `profiles/index.json`, `runs/<timestamp>.json` run report |
| Company profiles included | `profiles/` — see `profiles/index.json` (1000 companies, sampled with `python -m signalpost discover --count 1000 --seed 20261005`, list in `data/companies-1000.txt`) |
| Runtime | Python 3.11+ (tested on 3.14), `pip install -r requirements.txt` |
| Model / API details | Data: Brreg Enhetsregisteret + Regnskapsregisteret (NLOD 2.0), GLEIF LEI API (CC0), Wikidata SPARQL (CC0), the company's registry-declared website (robots.txt honoured). LLM: **optional**, off by default; `--llm` uses Anthropic `claude-opus-5-5` (Messages API, structured JSON output, effort low, server-side refusal fallback) only to phrase the explanation; output is rejected if it contains any number not present in the facts. |
| Expected run cost (100 companies/day) | Outbound requests ≈ 800 (measured mean 8.0 per company; cap 2000). Wall time ≈ 2 min at `--concurrency 8` (cap 45 min). External API cost **$0.00** without `--llm`; ≈ **$2.00** with `--llm` (hard cap `--max-cost 8`). |
| Daily-limit enforcement | built in: `--max-requests 2000 --time-budget 2700 --max-cost 8` are the defaults; the run stops cleanly and reports what it skipped |
| Update behaviour | re-running merges into stored profiles (added / changed / retracted, history kept); `python -m signalpost refresh` uses Brreg's update feed to re-run only changed or stale companies |
| Built with JBOX | no |

## How the scoring criteria are addressed

* **Find useful information (35)** — 7 sources, typically 60–170 facts per company: identity, legal form, dates, addresses, industry codes, employees, leadership and board, auditor, sub-units, up to 3 years of filed accounts (company and group), LEI, Wikipedia/Wikidata, website description, contacts and social profiles.
* **Match correctly with evidence (30)** — every fact has `evidence[]` with URL, source date, retrieval time and a `match` type; `matching.checks[]` records each identifier check (registry echo, filing org number, LEI registeredAs, Wikidata P2333, org number / company name on the website). Website facts that cannot be tied to the company are withheld and listed in `sources[].rejected`.
* **Update correctly (20)** — see `merge.py`: additions, changes (with history), retractions only when the source was successfully re-checked, otherwise facts are kept and flagged `stale`; `last_run_changes` and a change log per profile; Brreg update feed drives `refresh`.
* **Useful explanations (10)** — `explanation.text` in each profile (company brief, how each source was matched, what changed, what is missing); optional LLM brief with highlights and caveats.
* **Easy to use (5)** — one command, no keys, sensible defaults, `show` / `validate` / `stats` commands, JSON profiles with stable keys.

## Verifying locally

```sh
python -m unittest discover -s tests          # offline unit tests
python -m signalpost run 923609016            # one company (~10 requests, ~5 s)
python -m signalpost show 923609016
python -m signalpost validate                 # integrity checks over profiles/
python -m signalpost stats
```
