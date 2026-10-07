"""Concurrent native decodes retain the explicit 25 MP admission boundary."""

from __future__ import annotations

import json
import os
import subprocess
import sys

_CONCURRENT_NATIVE_DECODE = r"""
import hashlib
import io
import json
import sys
import threading
import warnings
from concurrent.futures import ThreadPoolExecutor
from PIL import Image
from app.services.screening.images import ImageNormalizationError, normalize_image

small_buffer = io.BytesIO()
with Image.new("RGB", (32, 24), (71, 109, 149)) as small_source:
    small_source.save(small_buffer, format="JPEG")
large_buffer = io.BytesIO()
with Image.new("RGB", (5001, 5000), (71, 109, 149)) as large_source:
    large_source.save(large_buffer, format="JPEG")
small_blob = small_buffer.getvalue()
large_blob = large_buffer.getvalue()
real_open = Image.open
small_entered = threading.Event()
large_entered = threading.Event()
small_completed = threading.Event()
role = threading.local()
timeline = []
timeline_lock = threading.Lock()

def note(event):
    with timeline_lock:
        timeline.append(event)

def scheduled_open(*args, **kwargs):
    if role.name == "small":
        note("small_inside_warning_context")
        small_entered.set()
        if not large_entered.wait(15):
            raise TimeoutError("large decode did not enter its warning context")
    else:
        note("large_inside_warning_context")
        large_entered.set()
        if not small_completed.wait(15):
            raise TimeoutError("small decode did not finish its warning context")
        note("large_real_pillow_open")
    return real_open(*args, **kwargs)

def small_decode():
    role.name = "small"
    normalized = normalize_image(small_blob, 1568, "image/jpeg")
    note("small_normalization_complete")
    small_completed.set()
    return {"width": normalized.width, "height": normalized.height,
            "sha256": normalized.sha256}

def large_decode():
    role.name = "large"
    try:
        normalized = normalize_image(large_blob, 1568, "image/jpeg")
    except ImageNormalizationError as exc:
        return {"outcome": "rejected", "reason": str(exc)}
    return {"outcome": "accepted", "width": normalized.width,
            "height": normalized.height, "sha256": normalized.sha256}

initial_filters = repr(warnings.filters)
Image.open = scheduled_open
try:
    with ThreadPoolExecutor(max_workers=2) as pool:
        small_future = pool.submit(small_decode)
        if not small_entered.wait(15):
            raise TimeoutError("small decode did not enter its warning context")
        large_future = pool.submit(large_decode)
        small = small_future.result(timeout=20)
        large = large_future.result(timeout=20)
finally:
    Image.open = real_open
print(json.dumps({"small": small, "large": large, "timeline": timeline,
    "large_source_dimensions": [5001, 5000], "large_source_pixels": 25005000,
    "large_jpeg_sha256": hashlib.sha256(large_blob).hexdigest(),
    "warnoptions": sys.warnoptions, "initial_filters": initial_filters,
    "python_version": sys.version,
    "context_aware_warnings": getattr(sys.flags, "context_aware_warnings", None)}))
"""


def test_concurrent_real_jpeg_normalizations_reject_over_budget_pixels() -> None:
    # A fresh child uses the production interpreter's warning policy rather than
    # pytest's in-process filters. The scheduling wrapper delegates every decode
    # to the actual Pillow factory and changes neither warning filters nor pixels.
    environment = os.environ.copy()
    completed = subprocess.run(
        [sys.executable, "-c", _CONCURRENT_NATIVE_DECODE],
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    result = json.loads(completed.stdout)
    assert result["warnoptions"] == [], "the native child must use the ordinary warning policy"
    assert result["small"]["width"] == 32 and result["small"]["height"] == 24
    assert result["timeline"] == [
        "small_inside_warning_context",
        "large_inside_warning_context",
        "small_normalization_complete",
        "large_real_pillow_open",
    ]
    assert result["large"]["outcome"] == "rejected", (
        "a genuine 25,005,000-pixel JPEG must remain rejected when another "
        f"normalization finishes first: {result['large']}"
    )
