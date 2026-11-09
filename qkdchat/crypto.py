"""Encryption / decryption modules (bottom of the conceptual design).

Two modes are offered so the trade-off can be discussed in the report:

* ``OneTimePad`` - XOR with fresh QKD key bits. Information-theoretically
  secure when every key bit is used once, which is the "textbook" pairing for
  QKD. Cost: one key bit per message bit, so the key pool drains quickly.
* ``AesGcm`` - AES-256-GCM with a 256-bit key distilled by QKD (via HKDF).
  Computationally secure and authenticated, uses far less key material; this
  is how most deployed QKD links are used in practice.
"""

from __future__ import annotations

import hmac
import os
from dataclasses import dataclass

import numpy as np
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


def bits_to_bytes(bits: np.ndarray) -> bytes:
    return np.packbits(bits.astype(np.uint8)).tobytes()


def bytes_to_bits(data: bytes) -> np.ndarray:
    return np.unpackbits(np.frombuffer(data, dtype=np.uint8))


@dataclass
class Ciphertext:
    mode: str
    data: bytes
    nonce: bytes = b""
    key_bits_used: int = 0

    def hex(self, limit: int = 48) -> str:
        h = self.data.hex()
        return h if len(h) <= limit else h[:limit] + "..."


class OneTimePad:
    name = "otp"

    @staticmethod
    def key_bits_needed(plaintext: bytes) -> int:
        return 8 * len(plaintext)

    @staticmethod
    def encrypt(plaintext: bytes, key_bits: np.ndarray) -> Ciphertext:
        if len(key_bits) < 8 * len(plaintext):
            raise ValueError("not enough key material for one-time pad")
        pad = bits_to_bytes(key_bits[: 8 * len(plaintext)])
        data = bytes(p ^ k for p, k in zip(plaintext, pad))
        return Ciphertext("otp", data, key_bits_used=8 * len(plaintext))

    @staticmethod
    def decrypt(ct: Ciphertext, key_bits: np.ndarray) -> bytes:
        pad = bits_to_bytes(key_bits[: 8 * len(ct.data)])
        return bytes(c ^ k for c, k in zip(ct.data, pad))


class AesGcm:
    name = "aes"
    KEY_BITS = 256

    @staticmethod
    def derive(key_bits: np.ndarray, info: bytes = b"qkd-chat aes-256-gcm") -> bytes:
        if len(key_bits) < AesGcm.KEY_BITS:
            raise ValueError("AES-256 needs at least 256 QKD key bits")
        ikm = bits_to_bytes(key_bits[: AesGcm.KEY_BITS])
        return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=info).derive(ikm)

    @staticmethod
    def encrypt(plaintext: bytes, key: bytes, aad: bytes = b"") -> Ciphertext:
        nonce = os.urandom(12)
        return Ciphertext("aes", AESGCM(key).encrypt(nonce, plaintext, aad), nonce)

    @staticmethod
    def decrypt(ct: Ciphertext, key: bytes, aad: bytes = b"") -> bytes:
        return AESGCM(key).decrypt(ct.nonce, ct.data, aad)


def constant_time_equal(a: bytes, b: bytes) -> bool:
    return hmac.compare_digest(a, b)
