"""A valid retained recurring conflict must return its same-series winner.

The delegated native workflow uses genuine constrained same-series occurrences,
public completion, attributed native verification, the declared no-spawn helper
mode, and a committed farm timezone correction. It preserves actual identities
and never substitutes a getter, SQL result, clock or warning policy.
"""

import httpx
import pytest
from sqlalchemy.exc import MultipleResultsFound, NoResultFound

from . import test_mutation38_native_recurrence_return_probe as native


async def test_valid_retained_recurring_conflict_resolves_exact_existing_winner(
    client: httpx.AsyncClient,
) -> None:
    run = native.test_native_conflict_winner_keeps_its_real_cached_identity_after_calendar_change
    try:
        await run(client)
    except (NoResultFound, MultipleResultsFound) as exc:
        pytest.fail(
            "A valid retained recurring conflict must resolve one existing winner "
            f"with the same farm, due date and series: {exc}"
        )
