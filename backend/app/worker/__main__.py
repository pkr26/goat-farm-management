"""``python -m app.worker`` entrypoint (see app/worker/__init__.py)."""

import asyncio

from . import main

raise SystemExit(asyncio.run(main()))
