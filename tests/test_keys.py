import base64
import hashlib
import json
import stat

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa

from persona_machine_mcp import keys


def pem(key):
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


@pytest.mark.parametrize("alg,bits,expected", [("ec", 2048, "ES256"), ("rsa", 2048, "RS256")])
def test_keypair_generation_roundtrip_and_permissions(tmp_path, alg, bits, expected):
    key = keys.generate_private_key(alg, bits)
    private, public = keys.write_keypair(tmp_path, key, force=False)
    loaded, detected = keys.load_private_key(private.read_bytes())
    assert detected == expected
    assert stat.S_IMODE(private.stat().st_mode) == 0o600
    assert stat.S_IMODE(public.stat().st_mode) == 0o644
    assert public.read_bytes() == keys.public_pem(loaded)
    decoded = serialization.load_pem_public_key(public.read_bytes())
    assert decoded.public_numbers() == key.public_key().public_numbers()
    assert b"BEGIN PRIVATE KEY" in private.read_bytes()
    with pytest.raises(FileExistsError):
        keys.write_keypair(tmp_path, keys.generate_private_key(alg, bits), force=False)
    assert private.read_bytes() == pem(key)
    replacement = keys.generate_private_key(alg, bits)
    keys.write_keypair(tmp_path, replacement, force=True)
    assert private.read_bytes() == pem(replacement)


@pytest.mark.parametrize("existing", ["private.pem", "public.pem"])
def test_never_partially_overwrite_existing_pair(tmp_path, existing):
    (tmp_path / existing).write_text("keep")
    with pytest.raises(FileExistsError):
        keys.write_keypair(tmp_path, keys.generate_private_key("ec", 2048), force=False)
    assert (tmp_path / existing).read_text() == "keep"
    assert len(list(tmp_path.iterdir())) == 1


@pytest.mark.parametrize(
    "key,match",
    [
        (ec.generate_private_key(ec.SECP384R1()), "P-256"),
        (rsa.generate_private_key(public_exponent=65537, key_size=1024), "2048"),
        (ed25519.Ed25519PrivateKey.generate(), "Unsupported"),
    ],
)
def test_reject_unsupported_keys(key, match):
    with pytest.raises(ValueError, match=match):
        keys.load_private_key(pem(key))


def test_bad_pem_and_generation_options():
    with pytest.raises(ValueError, match="PEM"):
        keys.load_private_key(b"not a key")
    with pytest.raises(ValueError, match="algorithm"):
        keys.generate_private_key("ed25519", 2048)
    with pytest.raises(ValueError, match="2048"):
        keys.generate_private_key("rsa", 1024)


@pytest.mark.parametrize("alg", ["ec", "rsa"])
def test_jwks_thumbprint_independently_computed(alg):
    key = keys.generate_private_key(alg, 2048)
    jwk = keys.jwks(key)["keys"][0]
    names = ("crv", "kty", "x", "y") if alg == "ec" else ("e", "kty", "n")
    canonical = json.dumps(
        {name: jwk[name] for name in names}, sort_keys=True, separators=(",", ":")
    )
    expected = base64.urlsafe_b64encode(hashlib.sha256(canonical.encode()).digest()).rstrip(b"=")
    assert jwk["kid"] == expected.decode()
    assert jwk["use"] == "sig"
    assert "d" not in jwk
    assert keys.jwks(key, "rotation-2")["keys"][0]["kid"] == "rotation-2"
    if alg == "ec":
        assert jwk["crv"] == "P-256"
        decoded_x = int.from_bytes(base64.urlsafe_b64decode(jwk["x"] + "="), "big")
        assert decoded_x == key.public_key().public_numbers().x
    else:
        assert jwk["e"] == "AQAB"
