"""Private Prometheus exposition, separate from metric collection."""

import hmac

from fastapi import APIRouter, HTTPException, Request, Response

from .. import metrics
from ..core.config import _has_nonblank_secret, get_settings
from ..deps import single_bearer_token

router = APIRouter()


@router.get("/metrics", include_in_schema=False)
async def operational_metrics(request: Request) -> Response:
    settings = get_settings()
    if not settings.metrics_enabled:
        raise HTTPException(status_code=404, detail="Not found")
    public = settings.environment != "production" and settings.metrics_public_enabled
    if not public:
        if not _has_nonblank_secret(settings.metrics_bearer_token):
            raise HTTPException(status_code=404, detail="Not found")
        expected = settings.metrics_bearer_token
        token = single_bearer_token(request)
        if (
            expected is None
            or token is None
            or not hmac.compare_digest(
                token.encode("utf-8"), expected.get_secret_value().encode("utf-8")
            )
        ):
            raise HTTPException(status_code=401, detail="Not authenticated")
    return Response(metrics.render(), media_type=metrics.CONTENT_TYPE_LATEST)
