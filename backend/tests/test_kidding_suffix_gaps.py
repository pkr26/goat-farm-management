"""Mutation-campaign gap test: kid-tag collision fallback shape (2026-09-30).

The readable "<doe>-K<n>" tag is preserved when free; on collision the
fallback is a cryptographically unpredictable `-A` + 12 hex chars. The
surviving ±1 mutants on `token_hex(6)` would ship 10- or 14-char suffixes
unnoticed — pinned by regex on a real collision.
"""

import re
from datetime import timedelta

import httpx

from app.utils import today

from .conftest import owner_with_farm
from .test_breeding_extended import make_animal
from .test_tasks_extended import (
    make_breeding,
    make_buck,
    make_doe,
    record_kidding,
    submit_ultrasound,
)

SUFFIX_RE = re.compile(r"^-A[0-9a-f]{12}$")


async def test_colliding_kid_tag_gets_twelve_hex_char_suffix(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client, email="kid-suffix@farm.in")
    doe = await make_doe(client, owner, "SUF-1")
    buck = await make_buck(client, owner)
    # An existing animal already holds the readable default "<doe>-K1", so
    # this litter's first kid must take the unpredictable fallback.
    await make_animal(client, owner, "SUF-1-K1")

    br = await make_breeding(client, owner, doe, buck, today() - timedelta(days=160))
    br = await submit_ultrasound(client, owner, br["id"], pregnant=True)
    record = await record_kidding(
        client, owner, br, today() - timedelta(days=10), kids=[{"sex": "F"}]
    )
    tag = record["kids"][0]["tag"]
    base, _, suffix = tag.rpartition("-A")
    assert base == "SUF-1-K1", tag
    assert SUFFIX_RE.match("-A" + suffix), tag
