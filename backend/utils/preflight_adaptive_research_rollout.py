#!/usr/bin/env python3
"""Read-only guard for retiring the legacy finite-grid experiment creator."""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from src.core.db import SessionLocal  # noqa: E402
from src.models.tables import ResearchExperiment  # noqa: E402


ACTIVE = {"queued", "running", "cancel_requested"}


def main() -> int:
    with SessionLocal() as db:
        rows = list(db.execute(select(ResearchExperiment)).scalars())
    legacy = [item for item in rows if (item.spec or {}).get("researchMode") != "adaptive_category"]
    counts = Counter(item.status for item in legacy)
    active = [item for item in legacy if item.status in ACTIVE]
    print(f"legacy_experiments={len(legacy)} statuses={dict(sorted(counts.items()))}")
    if active:
        print("rollout_blocked=true active_legacy_experiment_ids=" + ",".join(sorted(str(item.id) for item in active)))
        return 1
    print("rollout_blocked=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
