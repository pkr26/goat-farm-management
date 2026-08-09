"""Response schemas for the operational bucket board."""

from pydantic import BaseModel

from .summaries import BucketAnimalOut


class BucketBoardRow(BaseModel):
    bucket: str
    name: str
    who: str
    exit_rule: str
    daily_kg_per_head: float
    # A deterministic, bounded tag-order preview. Full profile access remains
    # on the paginated animals page and is independently protected by
    # ``animals.view``.
    animals: list[BucketAnimalOut]
    animals_total: int
    animals_limit: int
    animals_page_path: str
