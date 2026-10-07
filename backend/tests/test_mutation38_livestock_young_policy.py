"""Published goat profile policy used by native backend consumers."""

from app.models import GOAT_PROFILE


def test_goat_young_remain_with_the_dam_until_weaning() -> None:
    """The goat lifecycle keeps dependent kids with their doe until weaning."""
    assert GOAT_PROFILE.young_stay_with_dam is True
