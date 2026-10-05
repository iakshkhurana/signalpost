"""Profile storage: one JSON file per company plus an index."""
from __future__ import annotations

import json
import os
from typing import Iterable, Optional

from .models import now_iso


class Store:
    def __init__(self, root: str = "profiles"):
        self.root = root
        os.makedirs(root, exist_ok=True)

    def path(self, orgnr: str) -> str:
        return os.path.join(self.root, f"{orgnr}.json")

    def load(self, orgnr: str) -> Optional[dict]:
        p = self.path(orgnr)
        if not os.path.exists(p):
            return None
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def save(self, profile: dict) -> None:
        p = self.path(profile["orgnr"])
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(profile, f, ensure_ascii=False, indent=1, default=str)
        os.replace(tmp, p)

    def orgnrs(self) -> list[str]:
        return sorted(f[:-5] for f in os.listdir(self.root) if f.endswith(".json") and f[:-5].isdigit() and len(f) == 14)

    def iter_profiles(self) -> Iterable[dict]:
        for o in self.orgnrs():
            p = self.load(o)
            if p:
                yield p

    def rebuild_index(self) -> dict:
        rows = []
        for p in self.iter_profiles():
            active = [f for f in p.get("facts", []) if f.get("status") == "active"]
            rows.append({
                "orgnr": p["orgnr"],
                "name": p.get("name"),
                "facts": len(active),
                "sources_ok": [s["source"] for s in p.get("sources", []) if s.get("status") == "ok"],
                "match": (p.get("matching") or {}).get("overall"),
                "last_run": p.get("last_run"),
                "run_count": p.get("run_count", 1),
                "file": f"{p['orgnr']}.json",
            })
        index = {"generated_at": now_iso(), "count": len(rows), "companies": rows}
        with open(os.path.join(self.root, "index.json"), "w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False, indent=1)
        return index

    def export_jsonl(self, path: str) -> int:
        n = 0
        with open(path, "w", encoding="utf-8") as f:
            for p in self.iter_profiles():
                f.write(json.dumps(p, ensure_ascii=False, default=str) + "\n")
                n += 1
        return n


def write_run_report(report: dict, runs_dir: str = "runs") -> str:
    os.makedirs(runs_dir, exist_ok=True)
    name = report["started_at"].replace(":", "").replace("-", "")
    path = os.path.join(runs_dir, f"{name}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1, default=str)
    return path
