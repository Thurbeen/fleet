"""Fleet knows nothing of the products that sit above it.

A task's `origin` and `reply`, and an `owners.conf` rule, are opaque strings:
the convention for filling them in lives with whoever fills them in. A product's
name appearing in fleet's own code is how the one convention becomes a
dependency, so no module under `fleet/` or `scripts/` spells one.
"""

from __future__ import annotations

import re

from harness import REPO

NAMES = re.compile(r"\b(vega|slack)\b", re.IGNORECASE)


def test_no_module_names_a_product_that_fills_origin_or_reply():
    hits = [
        f"{path.relative_to(REPO)}:{n}"
        for root in ("fleet", "scripts")
        for path in sorted((REPO / root).rglob("*.py"))
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if NAMES.search(line)
    ]
    assert hits == [], f"fleet's code names a product that should only ever be an opaque value: {hits}"
