"""Image normalization: HD originals in, model-sized clean JPEGs out.

VLM token cost scales with image dimensions and every provider call carries
the same photo, so nothing is ever sent at original resolution. Re-encoding
through Pillow also drops EXIF (GPS/location of the farm) before the bytes
leave our infrastructure.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError

# Upper bound on decoded pixels, mirroring Pillow's own decompression-bomb
# guard; a "photo" that expands past this is corrupt or hostile.
MAX_DECODED_PIXELS = 200_000_000
JPEG_QUALITY = 85


class ImageNormalizationError(Exception):
    """The bytes are not a usable raster image."""


@dataclass(frozen=True)
class NormalizedImage:
    data: bytes
    width: int
    height: int
    sha256: str
    byte_size: int


def normalize_image(data: bytes, max_edge: int) -> NormalizedImage:
    """Decode, EXIF-orient, downscale to ``max_edge`` longest edge, and
    re-encode as a clean JPEG (metadata stripped)."""
    Image.MAX_IMAGE_PIXELS = MAX_DECODED_PIXELS
    try:
        with Image.open(io.BytesIO(data)) as source:
            # Apply the orientation the camera recorded before cropping
            # dimensions, or portrait photos rotate on re-save.
            oriented = ImageOps.exif_transpose(source)
            if oriented is None:
                oriented = source.copy()
            rgb = oriented.convert("RGB")
            rgb.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            rgb.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    except UnidentifiedImageError as exc:
        raise ImageNormalizationError("not a recognizable image format") from exc
    except OSError as exc:
        raise ImageNormalizationError(f"image decode failed: {exc}") from exc
    encoded = buffer.getvalue()
    return NormalizedImage(
        data=encoded,
        width=rgb.width,
        height=rgb.height,
        sha256=hashlib.sha256(encoded).hexdigest(),
        byte_size=len(encoded),
    )


# Detection boxes arrive margin-tight; a little breathing room keeps ears
# and tails in-frame after pixel rounding.
CROP_MARGIN_FRACTION = 0.05


class CropError(Exception):
    """The bytes could not be cropped as requested."""


@dataclass(frozen=True)
class CroppedImage:
    data: bytes
    width: int
    height: int
    sha256: str


def crop_image(source_jpeg: bytes, box_1000: tuple[int, int, int, int]) -> CroppedImage:
    """Crop one goat out of a normalized JPEG by its 0-1000 detection box.

    The margin fraction pads the box (ears/tails), the result is clamped to
    the actual frame, and a degenerate crop (rounding ate the whole goat)
    raises rather than sending the model an empty image.
    """
    from .detect import DetectionBox  # local: box type lives with the contract

    box = DetectionBox(*box_1000)
    try:
        with Image.open(io.BytesIO(source_jpeg)) as source:
            width, height = source.size
            margin_x = width * CROP_MARGIN_FRACTION
            margin_y = height * CROP_MARGIN_FRACTION
            left = max(0, round(box.x / 1000 * width - margin_x))
            top = max(0, round(box.y / 1000 * height - margin_y))
            right = min(width, round((box.x + box.w) / 1000 * width + margin_x))
            bottom = min(height, round((box.y + box.h) / 1000 * height + margin_y))
            if right - left < 8 or bottom - top < 8:
                raise CropError(
                    f"detection box {box_1000} crops to a degenerate region "
                    f"in a {width}x{height} image"
                )
            cropped = source.crop((left, top, right, bottom))
            cropped.load()
            buffer = io.BytesIO()
            cropped.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    except OSError as exc:
        raise CropError(f"crop decode failed: {exc}") from exc
    encoded = buffer.getvalue()
    return CroppedImage(
        data=encoded,
        width=cropped.width,
        height=cropped.height,
        sha256=hashlib.sha256(encoded).hexdigest(),
    )
