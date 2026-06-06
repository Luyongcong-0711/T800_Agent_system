from __future__ import annotations

import base64
import os
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


@dataclass(frozen=True)
class EncryptedPayload:
    ciphertext: str
    nonce: str
    tag: str
    key_version: str = "v1"
    alg: str = "AES-256-GCM"


def _b64encode(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.b64decode(value.encode("ascii"), validate=True)


def encode_master_key(raw_key: str) -> bytes:
    key = _b64decode(raw_key)
    if len(key) != 32:
        raise ValueError("AGENT_MASTER_KEY must decode to exactly 32 bytes.")
    return key


def generate_master_key() -> str:
    return _b64encode(os.urandom(32))


def encrypt_aes_256_gcm(
    plaintext: str,
    master_key: bytes,
    aad: str,
    key_version: str = "v1",
) -> EncryptedPayload:
    nonce = os.urandom(12)
    encrypted = AESGCM(master_key).encrypt(nonce, plaintext.encode("utf-8"), aad.encode("utf-8"))
    ciphertext, tag = encrypted[:-16], encrypted[-16:]
    return EncryptedPayload(
        ciphertext=_b64encode(ciphertext),
        nonce=_b64encode(nonce),
        tag=_b64encode(tag),
        key_version=key_version,
    )


def decrypt_aes_256_gcm(
    ciphertext: str,
    nonce: str,
    tag: str,
    master_key: bytes,
    aad: str,
) -> str:
    encrypted = _b64decode(ciphertext) + _b64decode(tag)
    decrypted = AESGCM(master_key).decrypt(_b64decode(nonce), encrypted, aad.encode("utf-8"))
    return decrypted.decode("utf-8")

