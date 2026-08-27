import pytest

from app.errors import AppError
from app.infrastructure.adapters.otp import InMemoryOtpStore
from app.infrastructure.adapters.security import PhoneProtector, normalize_phone


def test_phone_is_normalized_protected_and_recoverable() -> None:
    phone = normalize_phone("+8613800138000")
    protector = PhoneProtector("test-secret")
    encrypted = protector.encrypt(phone)

    assert phone == "+8613800138000"
    assert phone not in encrypted
    assert protector.decrypt(encrypted) == phone
    assert protector.lookup_hash(phone) == protector.lookup_hash(phone)


def test_invalid_phone_is_rejected() -> None:
    with pytest.raises(AppError) as exc:
        normalize_phone("123")

    assert exc.value.code == "INVALID_PHONE"


async def test_otp_is_single_use() -> None:
    store = InMemoryOtpStore("test-secret")
    phone = "+8613800138000"
    code = await store.issue(phone, "127.0.0.1", "LOGIN")

    await store.verify(phone, code, "LOGIN")
    with pytest.raises(AppError) as exc:
        await store.verify(phone, code, "LOGIN")

    assert exc.value.code == "OTP_EXPIRED"
