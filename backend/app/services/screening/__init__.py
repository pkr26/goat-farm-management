"""Disease screening pipeline.

Phase 1: S3 poll → normalize → gate model.
Phase 2: date-based provider rotation, second-opinion cross-check, and
per-region specialist refinement, ending in the vet review queue.
Phase 3: multi-goat detection splits photos into per-goat crops; each
crop cascades independently. Per-provider stats and the labeled-dataset
export close the loop to fine-tuning.
"""

from .detect import (
    DETECT_PROMPT_VERSION,
    DETECT_SYSTEM_PROMPT,
    DetectionBox,
    DetectionCallResult,
    DetectionParseError,
    detection_instruction,
    parse_detection_response,
)
from .gate import (
    GATE_PROMPT_VERSION,
    GateObservation,
    GateParseError,
    GateResponse,
    parse_gate_response,
)
from .images import (
    CropError,
    CroppedImage,
    ImageNormalizationError,
    NormalizedImage,
    crop_image,
    normalize_image,
)
from .pipeline import (
    CycleSummary,
    ParsedRawKey,
    normalized_derivative_key,
    parse_raw_key,
    pending_upload_abandoned_after,
    run_screening_cycle,
)
from .providers import (
    AnthropicProvider,
    GateCallResult,
    OpenAICompatibleProvider,
    ProviderAnswer,
    ProviderError,
    VisionProvider,
    build_provider_rotation,
)
from .providers import (
    gate as run_gate,
)
from .rotation import GateExhaustedError, GateOutcome, ProviderRotation
from .s3 import (
    PresignedPost,
    ScreeningObjectChangedError,
    ScreeningObjectInfo,
    ScreeningObjectMissingError,
    ScreeningObjectTooLargeError,
    ScreeningStorage,
    ScreeningStorageError,
    get_screening_storage,
    storage_for_settings,
)
from .specialists import (
    DISEASE_VOCABULARY,
    SPECIALIST_PROMPT_VERSIONS,
    SpecialistCallResult,
    SpecialistCondition,
    SpecialistKind,
    SpecialistParseError,
    SpecialistResponse,
    parse_specialist_response,
    run_specialist,
    specialist_for_region,
    specialist_prompt,
)

__all__ = [
    "DETECT_PROMPT_VERSION",
    "DETECT_SYSTEM_PROMPT",
    "DISEASE_VOCABULARY",
    "GATE_PROMPT_VERSION",
    "SPECIALIST_PROMPT_VERSIONS",
    "AnthropicProvider",
    "CropError",
    "CroppedImage",
    "CycleSummary",
    "DetectionBox",
    "DetectionCallResult",
    "DetectionParseError",
    "GateCallResult",
    "GateExhaustedError",
    "GateObservation",
    "GateOutcome",
    "GateParseError",
    "GateResponse",
    "ImageNormalizationError",
    "NormalizedImage",
    "OpenAICompatibleProvider",
    "ParsedRawKey",
    "PresignedPost",
    "ProviderAnswer",
    "ProviderError",
    "ProviderRotation",
    "ScreeningObjectChangedError",
    "ScreeningObjectInfo",
    "ScreeningObjectMissingError",
    "ScreeningObjectTooLargeError",
    "ScreeningStorage",
    "ScreeningStorageError",
    "SpecialistCallResult",
    "SpecialistCondition",
    "SpecialistKind",
    "SpecialistParseError",
    "SpecialistResponse",
    "VisionProvider",
    "build_provider_rotation",
    "crop_image",
    "detection_instruction",
    "get_screening_storage",
    "normalize_image",
    "normalized_derivative_key",
    "parse_detection_response",
    "parse_gate_response",
    "parse_raw_key",
    "parse_specialist_response",
    "pending_upload_abandoned_after",
    "run_gate",
    "run_screening_cycle",
    "run_specialist",
    "specialist_for_region",
    "specialist_prompt",
    "storage_for_settings",
]
