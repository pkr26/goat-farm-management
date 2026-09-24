"""Breeding-fence gap tests (2026-09-23 verification plan, category 3).

The banned pairings (parent-offspring, full siblings, grandparent-grandchild
both directions, avuncular) and the permitted half-sibling/unrelated cases
are pinned in test_redteam_domain_fixes. These two tests pin the remaining
fence boundaries so nobody "fixes" the fence too tight or too loose later:

- first cousins share grandparents but no parent — the 2-generation walk
  must let them through (the documented policy boundary);
- purchased animals with no recorded ancestry are fail-open by design: the
  fence can only reason about lineage the herd actually recorded. That
  deliberate behaviour is asserted here so a future tightening shows up as
  a conscious decision instead of a silent regression.
"""

import httpx

from app.db import get_sessionmaker
from app.models import Animal

from .conftest import owner_with_farm
from .test_breeding_extended import make_buck, make_doe, post_breeding


async def _set_lineage(animal_id: int, *, sire_id: int | None, dam_id: int | None) -> None:
    """The public API writes lineage only through kidding; fence tests model
    rows a real farm accumulates as kids grow into the breeding herd."""
    async with get_sessionmaker()() as db:
        animal = await db.get(Animal, animal_id)
        assert animal is not None
        animal.sire_id = sire_id
        animal.dam_id = dam_id
        await db.commit()


async def test_first_cousin_mating_remains_permitted(client: httpx.AsyncClient) -> None:
    """Cousins: children of two siblings. They share grandparents, not
    parents — outside the fence's documented 2-generation ban set."""
    owner = await owner_with_farm(client, email="cousin@farm.in")
    grandsire = await make_buck(client, owner, tag="CO-GS")
    granddam = await make_doe(client, owner, tag="CO-GD")

    # Two siblings (a buck and a doe), children of the grandparents.
    brother = await make_buck(client, owner, tag="CO-BRO")
    sister = await make_doe(client, owner, tag="CO-SIS")
    for sibling in (brother, sister):
        await _set_lineage(sibling["id"], sire_id=grandsire["id"], dam_id=granddam["id"])

    # Each sibling has a child with an unrelated outsider.
    outsider_doe = await make_doe(client, owner, tag="CO-OUT-F")
    outsider_buck = await make_buck(client, owner, tag="CO-OUT-M")
    cousin_buck = await make_buck(client, owner, tag="CO-COUSIN-M")
    cousin_doe = await make_doe(client, owner, tag="CO-COUSIN-F")
    await _set_lineage(cousin_buck["id"], sire_id=brother["id"], dam_id=outsider_doe["id"])
    await _set_lineage(cousin_doe["id"], sire_id=outsider_buck["id"], dam_id=sister["id"])

    resp = await post_breeding(client, owner, cousin_doe["id"], cousin_buck["id"])
    assert resp.status_code == 201, (
        f"first-cousin mating refused — the fence over-tightened: {resp.text[:200]}"
    )

    # Contrast: the sibling pair themselves (full siblings) remain banned.
    sibling_ban = await post_breeding(client, owner, sister["id"], brother["id"])
    assert sibling_ban.status_code == 409, sibling_ban.text
    assert "inbreeding" in sibling_ban.json()["detail"].lower()


async def test_purchased_unknown_ancestry_mating_is_fail_open_by_design(
    client: httpx.AsyncClient,
) -> None:
    """Two purchased animals with no dam/sire on record: the fence cannot
    see kinship that was never recorded, so the mating is allowed. This is
    the documented fail-open stance (the fence reasons only over recorded
    lineage); if policy ever flips to fail-closed, this test failing is the
    conversation starter."""
    owner = await owner_with_farm(client, email="unknown@farm.in")
    doe = await make_doe(client, owner, tag="UNK-F")
    buck = await make_buck(client, owner, tag="UNK-M")
    async with get_sessionmaker()() as db:
        for animal_id in (doe["id"], buck["id"]):
            row = await db.get(Animal, animal_id)
            assert row is not None and row.sire_id is None and row.dam_id is None

    resp = await post_breeding(client, owner, doe["id"], buck["id"])
    assert resp.status_code == 201, (
        f"no-ancestry mating refused — fence silently tightened to fail-closed: {resp.text[:200]}"
    )
