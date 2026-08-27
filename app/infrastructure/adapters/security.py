from __future__ import annotations

import base64
import hashlib
import hmac

import phonenumbers
from cryptography.fernet import Fernet, InvalidToken

from app.errors import AppError


def normalize_phone(raw_phone: str) -> str:
    try:
        parsed = phonenumbers.parse(raw_phone, None)
    except phonenumbers.NumberParseException as exc:
        raise AppError(422, "INVALID_PHONE", "phone must include an international prefix") from exc
    if not phonenumbers.is_valid_number(parsed):
        raise AppError(422, "INVALID_PHONE", "phone number is invalid")
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


class PhoneProtector:
    def __init__(self, hmac_secret: str, encryption_key: str = "") -> None:
        self._hmac_secret = hmac_secret.encode()
        key = encryption_key.encode() if encryption_key else self._derive_fernet_key(hmac_secret)
        self._fernet = Fernet(key)

    def lookup_hash(self, phone: str) -> str:
        return hmac.new(self._hmac_secret, phone.encode(), hashlib.sha256).hexdigest()

    def encrypt(self, phone: str) -> str:
        return self._fernet.encrypt(phone.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext.encode()).decode()
        except InvalidToken as exc:
            raise ValueError("phone ciphertext cannot be decrypted") from exc

    @staticmethod
    def _derive_fernet_key(secret: str) -> bytes:
        return base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
