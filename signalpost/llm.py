"""Optional LLM explanation layer (Anthropic Claude).

Off by default. Enabled with --llm when ANTHROPIC_API_KEY (or an `ant auth
login` profile) is available. The model only rewrites facts we already hold:
its output is checked so that every number of four or more digits it mentions
exists in the input facts; otherwise the deterministic template is used. It
never adds facts to the profile.
"""
from __future__ import annotations

import json
import os
import re
from typing import Optional

from .explain import fmt_amount
from .http import Budget

DEFAULT_MODEL = os.environ.get("SIGNALPOST_MODEL", "claude-opus-5-5")

# USD per million tokens (input, output). Used for the declared-cost accounting.
PRICES = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
}

SYSTEM = """You write short, factual company briefs for analysts.
You are given a JSON list of verified facts about one Norwegian company, each with its source.
Rules:
- Use ONLY the facts provided. Do not add, infer, estimate or round any value.
- Financial amounts: copy the 'display' string exactly as given (currency and digits). Never convert currencies or units.
- Mention the year and whether a financial figure is a company or group (konsern) statement.
- Name the source type when it matters (registry, annual accounts, LEI, Wikidata, company website).
- If sources disagree, say so plainly.
- Write in English. Be concrete and brief."""

SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "2-4 sentence plain-language brief of what the company is and does."},
        "highlights": {"type": "array", "items": {"type": "string"}, "description": "3-6 bullet facts worth knowing, each citing its source type."},
        "caveats": {"type": "array", "items": {"type": "string"}, "description": "Data gaps, stale items, or source disagreements. Empty if none."},
    },
    "required": ["summary", "highlights", "caveats"],
    "additionalProperties": False,
}

_NUM = re.compile(r"\d[\d\s., ]*\d|\d")


class Explainer:
    def __init__(self, budget: Budget, model: str = DEFAULT_MODEL):
        self.budget = budget
        self.model = model
        self.disabled_reason: Optional[str] = None
        self._client = None
        try:
            import anthropic  # noqa: F401
        except ImportError:
            self.disabled_reason = "anthropic SDK not installed (pip install anthropic)"

    def available(self) -> bool:
        return self.disabled_reason is None

    def _client_or_none(self):
        if self._client is None and self.available():
            import anthropic
            try:
                self._client = anthropic.AsyncAnthropic()
            except Exception as e:  # typically: no ANTHROPIC_API_KEY / profile available
                self.disabled_reason = f"no credentials for the Anthropic API ({type(e).__name__}); set ANTHROPIC_API_KEY"
                return None
        return self._client

    @staticmethod
    def _prepare(profile: dict) -> tuple[list[dict], set[str]]:
        facts = []
        allowed: set[str] = set()
        for f in profile.get("facts", []):
            if f.get("status") != "active":
                continue
            v = f["value"]
            entry = {"key": f["key"], "value": v, "source": f["evidence"][0]["source"] if f.get("evidence") else None}
            if f["key"].startswith("finance.") and isinstance(v, dict) and v.get("amount") is not None:
                entry["display"] = fmt_amount(v)
            facts.append(entry)
        blob = json.dumps(facts, ensure_ascii=False, default=str)
        for tok in _NUM.findall(blob):
            digits = re.sub(r"\D", "", tok)
            if digits:
                allowed.add(digits)
        for e in facts:
            if "display" in e:
                allowed.add(re.sub(r"\D", "", e["display"]))
        allowed.add(profile["orgnr"])
        return facts, allowed

    @staticmethod
    def _grounded(text: str, allowed: set[str]) -> tuple[bool, list[str]]:
        bad = []
        for tok in _NUM.findall(text):
            digits = re.sub(r"\D", "", tok)
            if len(digits) >= 4 and digits not in allowed:
                # allow sub-strings of allowed numbers (e.g. the year inside a date, or '107,174' from '107174000000')
                if not any(digits in a for a in allowed):
                    bad.append(tok.strip())
        return (not bad), bad

    async def explain(self, profile: dict) -> Optional[dict]:
        client = self._client_or_none()
        if client is None:
            return None
        import anthropic
        facts, allowed = self._prepare(profile)
        if not facts:
            return None
        try:
            self.budget.check()
            resp = await client.beta.messages.create(
                model=self.model,
                max_tokens=1500,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                system=SYSTEM,
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
                messages=[{"role": "user", "content": "Facts for org.nr " + profile["orgnr"] + ":\n" +
                           json.dumps(facts, ensure_ascii=False, default=str)}],
            )
        except anthropic.AuthenticationError as e:
            self.disabled_reason = f"authentication failed: {e.message}"
            return None
        except anthropic.BadRequestError as e:
            self.disabled_reason = f"request rejected: {e.message}"
            return None
        except anthropic.RateLimitError:
            return {"error": "rate limited; template used"}
        except anthropic.APIStatusError as e:
            return {"error": f"API error {e.status_code}; template used"}
        except anthropic.APIConnectionError:
            return {"error": "network error; template used"}
        except Exception as e:  # e.g. TypeError from credential resolution when no key/profile exists
            self.disabled_reason = f"LLM call failed ({type(e).__name__}: {str(e)[:120]}); set ANTHROPIC_API_KEY"
            return None
        usage = getattr(resp, "usage", None)
        pin, pout = PRICES.get(self.model, PRICES["claude-opus-5-5"])
        cost = 0.0
        if usage:
            cost = (getattr(usage, "input_tokens", 0) or 0) * pin / 1e6 + (getattr(usage, "output_tokens", 0) or 0) * pout / 1e6
        self.budget.llm_calls += 1
        self.budget.llm_cost_usd += cost
        if resp.stop_reason == "refusal":
            return {"error": "model declined; template used", "cost_usd": cost}
        text = next((b.text for b in resp.content if b.type == "text"), None)
        if not text:
            return {"error": "empty response; template used", "cost_usd": cost}
        try:
            data = json.loads(text)
        except ValueError:
            return {"error": "non-JSON response; template used", "cost_usd": cost}
        joined = " ".join([data.get("summary", "")] + data.get("highlights", []) + data.get("caveats", []))
        ok, bad = self._grounded(joined, allowed)
        if not ok:
            return {"error": f"ungrounded numbers {bad[:3]} in model output; template used", "cost_usd": cost}
        return {"summary": data.get("summary"), "highlights": data.get("highlights", []),
                "caveats": data.get("caveats", []), "model": self.model, "cost_usd": round(cost, 5)}
