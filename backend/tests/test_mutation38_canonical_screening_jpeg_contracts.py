"""Canonical full-frame and crop artifacts preserve their fixed JPEG85 quantizer."""

import hashlib
import io

import pytest
from PIL import Image
from PIL.JpegImagePlugin import JpegImageFile

from app.services.screening.images import (
    CropError,
    ImageNormalizationError,
    crop_image,
    normalize_image,
)


def test_camera_normalization_has_the_known_canonical_jpeg_quantization() -> None:
    source = io.BytesIO()
    with Image.new("RGB", (240, 160), (29, 91, 173)) as camera:
        camera.save(source, format="PNG")
    try:
        normalized = normalize_image(source.getvalue(), 120, "image/png")
    except ImageNormalizationError as exc:
        pytest.fail(f"A valid small RGB camera frame must yield its canonical derivative: {exc}")
    assert (normalized.width, normalized.height) == (120, 80)
    assert normalized.sha256 == hashlib.sha256(normalized.data).hexdigest()
    assert normalized.byte_size == len(normalized.data)
    with Image.open(io.BytesIO(normalized.data)) as artifact:
        assert isinstance(artifact, JpegImageFile)
        # These are wire DQT coefficients for the fixed standard85 quantizer,
        # independent of the application's JPEG_QUALITY variable. Canonical
        # image identity, store references and retries consume these bytes.
        assert artifact.quantization[0][:8] == [5, 3, 3, 5, 7, 12, 15, 18]
        assert artifact.quantization[1][:8] == [5, 5, 7, 14, 30, 30, 30, 30]
        assert artifact.getexif() == {}


def test_valid_goat_crop_uses_the_same_known_canonical_jpeg_quantization() -> None:
    source = io.BytesIO()
    with Image.new("RGB", (240, 160), (29, 91, 173)) as frame:
        frame.save(source, format="JPEG", quality=95)
    try:
        cropped = crop_image(source.getvalue(), (200, 200, 400, 400))
    except CropError as exc:
        pytest.fail(f"A valid in-frame goat crop must yield its canonical derivative: {exc}")
    assert cropped.sha256 == hashlib.sha256(cropped.data).hexdigest()
    with Image.open(io.BytesIO(cropped.data)) as artifact:
        assert isinstance(artifact, JpegImageFile)
        assert artifact.quantization[0][:8] == [5, 3, 3, 5, 7, 12, 15, 18]
        assert artifact.quantization[1][:8] == [5, 5, 7, 14, 30, 30, 30, 30]
