from types import SimpleNamespace

import pytest

from backend.services.admin_security import (
    decrypt_totp_secret,
    encrypt_totp_secret,
    is_totp_secret_encrypted,
    upgrade_totp_secret_encryption,
    verify_stored_totp,
)

_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"


def test_totp_secret_is_encrypted_and_round_trips():
    encrypted = encrypt_totp_secret(7, _SECRET)

    assert is_totp_secret_encrypted(encrypted)
    assert _SECRET not in encrypted
    assert decrypt_totp_secret(7, encrypted) == _SECRET


def test_encrypted_totp_secret_is_bound_to_admin_id():
    encrypted = encrypt_totp_secret(7, _SECRET)

    with pytest.raises(ValueError, match="cannot be decrypted"):
        decrypt_totp_secret(8, encrypted)


def test_stored_encrypted_totp_verifies_standard_vector():
    encrypted = encrypt_totp_secret(7, _SECRET)

    assert verify_stored_totp(7, encrypted, "287082", at_time=59, window=0)
    assert not verify_stored_totp(7, encrypted, "287083", at_time=59, window=0)


def test_legacy_plaintext_secret_is_upgraded_in_place():
    row = SimpleNamespace(admin_id=7, secret=_SECRET)

    assert upgrade_totp_secret_encryption(row) is True
    assert is_totp_secret_encrypted(row.secret)
    assert decrypt_totp_secret(7, row.secret) == _SECRET
    assert upgrade_totp_secret_encryption(row) is False
