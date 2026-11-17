"""Key generation, strict algorithm validation, and public-only JWKS export."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa

PrivateKey = ec.EllipticCurvePrivateKey | rsa.RSAPrivateKey


def generate_private_key(alg: str = "ec", bits: int = 2048) -> PrivateKey:
    if alg == "ec":
        return ec.generate_private_key(ec.SECP256R1())
    if alg != "rsa":
        raise ValueError("Unsupported algorithm; choose ec or rsa")
    if bits not in (2048, 3072, 4096):
        raise ValueError("RSA bits must be 2048, 3072, or 4096")
    return rsa.generate_private_key(public_exponent=65537, key_size=bits)


def load_private_key(pem: bytes | str) -> tuple[PrivateKey, str]:
    try:
        key = serialization.load_pem_private_key(
            pem.encode() if isinstance(pem, str) else pem, password=None
        )
    except (ValueError, TypeError) as exc:
        raise ValueError("Invalid or encrypted private PEM; use an unencrypted PKCS#8 key") from exc
    if isinstance(key, ec.EllipticCurvePrivateKey):
        if not isinstance(key.curve, ec.SECP256R1):
            raise ValueError("EC key must be P-256 (secp256r1)")
        return key, "ES256"
    if isinstance(key, rsa.RSAPrivateKey):
        if key.key_size < 2048:
            raise ValueError("RSA key must be >= 2048 bits")
        return key, "RS256"
    raise ValueError("Unsupported key type; use EC P-256 or RSA 2048+")


def public_pem(key: PrivateKey) -> bytes:
    return key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )


def write_private_file(path: Path, data: bytes, *, mode: int = 0o600, force: bool = True) -> None:
    """Write atomically with permissions set before data; never follow destination symlinks."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".persona-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.chmod(temporary, mode)
            stream.write(data)
        if force:
            os.replace(temporary, path)
        else:
            # An atomic no-clobber install also protects against concurrent keygen.
            os.link(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_keypair(directory: str | Path, key: PrivateKey, force: bool = False) -> tuple[Path, Path]:
    directory = Path(directory).expanduser()
    private, public = directory / "private.pem", directory / "public.pem"
    if not force and (os.path.lexists(private) or os.path.lexists(public)):
        raise FileExistsError("Key files already exist; use --force only when rotating")
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    write_private_file(private, pem, force=force)
    write_private_file(public, public_pem(key), mode=0o644, force=force)
    return private, public


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _integer(value: int) -> str:
    return _base64url(value.to_bytes((value.bit_length() + 7) // 8, "big"))


def jwks(key: PrivateKey, kid: str | None = None) -> dict:
    if isinstance(key, ec.EllipticCurvePrivateKey):
        numbers = key.public_key().public_numbers()
        public = {
            "crv": "P-256",
            "kty": "EC",
            "x": _base64url(numbers.x.to_bytes(32, "big")),
            "y": _base64url(numbers.y.to_bytes(32, "big")),
        }
        alg = "ES256"
    else:
        numbers = key.public_key().public_numbers()
        public = {"e": _integer(numbers.e), "kty": "RSA", "n": _integer(numbers.n)}
        alg = "RS256"
    canonical = json.dumps(public, sort_keys=True, separators=(",", ":")).encode("utf-8")
    thumbprint = _base64url(hashlib.sha256(canonical).digest())
    return {
        "keys": [{**public, "kid": thumbprint if kid is None else kid, "use": "sig", "alg": alg}]
    }
