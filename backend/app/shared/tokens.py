"""Password hashing and signed access tokens, on the standard library only.

Both pieces are small and well-specified, so they are implemented here rather
than pulling in another dependency:

* passwords use ``hashlib.scrypt`` — a memory-hard KDF, with a per-password
  salt and a constant-time comparison;
* access tokens are compact JWTs restricted to HS256. The signature is
  verified *before* any claim is read, and the algorithm is pinned, so the
  "alg: none" and algorithm-confusion attacks do not apply.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any

from .errors import FluxError

# scrypt parameters: ~16 MB and a few ms per hash on commodity hardware.
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32
_SALT_BYTES = 16

_ALGORITHM = "HS256"


class TokenError(FluxError):
    status_code = 401
    code = "invalid_token"


# --------------------------------------------------------------------------
# passwords
# --------------------------------------------------------------------------
def hash_password(password: str) -> str:
    """Return ``scrypt$n$r$p$salt$digest``, all base64url."""
    if not password or len(password) < 8:
        raise ValueError("a password must be at least 8 characters")
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
    )
    return "$".join(
        ["scrypt", str(_SCRYPT_N), str(_SCRYPT_R), str(_SCRYPT_P),
         _b64encode(salt), _b64encode(digest)]
    )


def verify_password(password: str, encoded: str) -> bool:
    """Constant-time check of a password against a stored hash."""
    try:
        scheme, raw_n, raw_r, raw_p, raw_salt, raw_digest = encoded.split("$")
        if scheme != "scrypt":
            return False
        candidate = hashlib.scrypt(
            password.encode("utf-8"),
            salt=_b64decode(raw_salt),
            n=int(raw_n),
            r=int(raw_r),
            p=int(raw_p),
            dklen=len(_b64decode(raw_digest)),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate, _b64decode(raw_digest))


# --------------------------------------------------------------------------
# access tokens
# --------------------------------------------------------------------------
def encode_token(claims: dict[str, Any], *, secret: str, expires_in: int) -> str:
    issued = int(time.time())
    payload = {
        **claims,
        "iat": issued,
        "exp": issued + int(expires_in),
        "jti": secrets.token_urlsafe(8),
    }
    header = {"alg": _ALGORITHM, "typ": "JWT"}
    segments = [_b64encode(_compact(header)), _b64encode(_compact(payload))]
    signing_input = ".".join(segments).encode("ascii")
    segments.append(_b64encode(_sign(signing_input, secret)))
    return ".".join(segments)


def decode_token(token: str, *, secret: str) -> dict[str, Any]:
    """Verify the signature and expiry, then return the claims."""
    try:
        header_segment, payload_segment, signature_segment = token.split(".")
    except ValueError as exc:
        raise TokenError("the access token is malformed") from exc

    signing_input = f"{header_segment}.{payload_segment}".encode("ascii")
    try:
        signature = _b64decode(signature_segment)
    except (ValueError, TypeError) as exc:
        raise TokenError("the access token is malformed") from exc

    #  Signature first: nothing inside the token is trusted until it verifies.
    if not hmac.compare_digest(signature, _sign(signing_input, secret)):
        raise TokenError("the access token signature does not match")

    try:
        header = json.loads(_b64decode(header_segment))
        claims = json.loads(_b64decode(payload_segment))
    except (ValueError, TypeError) as exc:
        raise TokenError("the access token is malformed") from exc

    if header.get("alg") != _ALGORITHM:
        raise TokenError("unsupported token algorithm")
    if int(claims.get("exp", 0)) <= int(time.time()):
        raise TokenError("the access token has expired")
    return claims


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _sign(signing_input: bytes, secret: str) -> bytes:
    return hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()


def _compact(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + padding)
