"""Image normalization: HD originals in, model-sized clean JPEGs out.

VLM token cost scales with image dimensions and every provider call carries
the same photo, so nothing is ever sent at original resolution. Re-encoding
through Pillow also drops EXIF (GPS/location of the farm) before the bytes
leave our infrastructure.
"""

from __future__ import annotations

import hashlib
import io
import warnings
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError

# Decode is deliberately much tighter than the worker's 1 GiB container.
# A 25 MP RGB frame is already ~75 MiB before Pillow's orientation/conversion
# intermediates; the former 200 MP cap allowed a single compressed image to
# exhaust a worker.  Cameras that exceed this need client-side downscaling.
MAX_DECODED_PIXELS = 25_000_000
MAX_DECODED_EDGE_PX = 10_000
JPEG_QUALITY = 85

_ALLOWED_SOURCE_FORMATS = {"JPEG", "PNG"}
_FORMAT_BY_CONTENT_TYPE = {"image/jpeg": "JPEG", "image/png": "PNG"}
_MAGIC_BY_CONTENT_TYPE = {
    "image/jpeg": b"\xff\xd8\xff",
    "image/png": b"\x89PNG\r\n\x1a\n",
}

# Pillow's guard is process-global.  Set the conservative ceiling once at
# import rather than mutating it during every threaded worker decode.
Image.MAX_IMAGE_PIXELS = MAX_DECODED_PIXELS


class ImageNormalizationError(Exception):
    """The bytes are not a usable raster image."""


@dataclass(frozen=True)
class NormalizedImage:
    data: bytes
    width: int
    height: int
    sha256: str
    byte_size: int


def normalize_image(
    data: bytes, max_edge: int, expected_content_type: str | None = None
) -> NormalizedImage:
    """Decode, EXIF-orient, downscale to ``max_edge`` longest edge, and
    re-encode as a clean JPEG (metadata stripped).

    Only JPEG and PNG sources are accepted.  The object-store content type is
    policy-bound for new uploads, and the magic bytes plus Pillow's detected
    format must agree before any full decode happens.  This keeps unneeded
    parsers (PSD, TIFF, GIF, PDF-like polyglots) out of the worker's attack
    surface.
    """
    if expected_content_type is not None:
        expected_format = _FORMAT_BY_CONTENT_TYPE.get(expected_content_type)
        magic = _MAGIC_BY_CONTENT_TYPE.get(expected_content_type)
        if expected_format is None or magic is None:
            raise ImageNormalizationError("unsupported declared image content type")
        if not data.startswith(magic):
            raise ImageNormalizationError("image magic bytes do not match declared content type")
    else:
        expected_format = None
    try:
        # Pillow reports a warning at the threshold and raises only at twice
        # it.  Promote the warning so a 25–50 MP bomb cannot be decoded.
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as source:
                source_format = source.format
                if source_format not in _ALLOWED_SOURCE_FORMATS:
                    raise ImageNormalizationError(
                        f"unsupported image format {source_format!r}; only JPEG and PNG are allowed"
                    )
                if expected_format is not None and source_format != expected_format:
                    raise ImageNormalizationError(
                        "decoded image format does not match declared content type"
                    )
                source_width, source_height = source.size
                if (
                    source_width <= 0
                    or source_height <= 0
                    or source_width * source_height > MAX_DECODED_PIXELS
                    or max(source_width, source_height) > MAX_DECODED_EDGE_PX
                ):
                    raise ImageNormalizationError("image exceeds the decode dimension budget")
                # Force the decoder to validate all compressed pixels before
                # allocating conversion/orientation intermediates.
                source.load()
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
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        # Not an OSError subclass: without this arm a hostile "photo" whose
        # header claims billions of pixels must be rejected permanently, not
        # escape as a generic pipeline failure and retry hourly.
        raise ImageNormalizationError(f"image exceeds the decode pixel budget: {exc}") from exc
    except (OSError, SyntaxError, ValueError) as exc:
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
