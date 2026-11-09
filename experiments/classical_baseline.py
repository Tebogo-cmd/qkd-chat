"""Classical counterpart of the QKD link: X25519 Diffie-Hellman + AES-256-GCM.

Used for research question 3 / objective 2 - comparing a quantum and a classical
way of establishing the same 256-bit shared key, both in run time and in the
*shape* of the code. The classical version is ~10 lines and deterministic;
its security rests on the hardness of the elliptic-curve discrete logarithm,
which Shor's algorithm would break on a large fault-tolerant quantum computer.
BB84's security instead rests on the no-cloning theorem.

    python experiments/classical_baseline.py
"""

from __future__ import annotations

import os
import time

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


def x25519_shared_key() -> tuple[bytes, bytes, int]:
    """One complete classical key agreement. Returns (alice_key, bob_key, public_bytes)."""
    a_priv, b_priv = X25519PrivateKey.generate(), X25519PrivateKey.generate()
    a_pub, b_pub = a_priv.public_key(), b_priv.public_key()          # exchanged publicly
    raw = serialization.Encoding.Raw, serialization.PublicFormat.Raw
    public_bytes = len(a_pub.public_bytes(*raw)) + len(b_pub.public_bytes(*raw))
    kdf = lambda s: HKDF(hashes.SHA256(), 32, None, b"classical chat").derive(s)  # noqa: E731
    return kdf(a_priv.exchange(b_pub)), kdf(b_priv.exchange(a_pub)), public_bytes


def benchmark_x25519(trials: int = 500) -> dict:
    t0 = time.perf_counter()
    for _ in range(trials):
        ka, kb, pub = x25519_shared_key()
        assert ka == kb
    return {"seconds_per_exchange": (time.perf_counter() - t0) / trials, "public_bytes": pub}


def demo() -> None:
    ka, kb, pub = x25519_shared_key()
    nonce = os.urandom(12)
    ct = AESGCM(ka).encrypt(nonce, b"Hello Bob (classically)", None)
    print("shared key equal:", ka == kb, "| public bytes:", pub)
    print("ciphertext:", ct.hex())
    print("Bob reads :", AESGCM(kb).decrypt(nonce, ct, None).decode())
    print(f"time per exchange: {1e3 * benchmark_x25519()['seconds_per_exchange']:.3f} ms")


if __name__ == "__main__":
    demo()
