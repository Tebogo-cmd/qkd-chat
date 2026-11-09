"""QKD-secured messaging link between Alice and Bob (application layer).

``SecureLink`` owns both parties' key pools, runs BB84 whenever more key
material is needed, and encrypts / decrypts chat messages with either a
one-time pad or AES-256-GCM. It is UI-agnostic: the CLI and the notebooks both
drive it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .crypto import AesGcm, Ciphertext, OneTimePad
from .keypool import KeyPool
from .noise import ChannelNoise
from .protocol import BB84Config, BB84Protocol, KeyExchangeReport
from .quantum import Eavesdropper

PARTIES = ("Alice", "Bob")


class EavesdropperDetected(RuntimeError):
    def __init__(self, report: KeyExchangeReport):
        super().__init__(report.abort_reason)
        self.report = report


@dataclass
class Delivery:
    sender: str
    receiver: str
    plaintext: str
    ciphertext: Ciphertext
    decrypted: str
    key_bits_used: int
    rekeyed: list[KeyExchangeReport] = field(default_factory=list)
    eve_view: str = ""

    @property
    def ok(self) -> bool:
        return self.plaintext == self.decrypted


class SecureLink:
    def __init__(
        self,
        mode: str = "otp",
        config: BB84Config | None = None,
        noise: ChannelNoise | None = None,
        hardware: str | None = None,
        eve: Eavesdropper | None = None,
        seed: int | None = None,
        rng_kind: str = "pseudo",
        max_attempts: int = 3,
    ):
        if mode not in ("otp", "aes"):
            raise ValueError("mode must be 'otp' or 'aes'")
        self.mode = mode
        self.config = config or BB84Config()
        self.noise = noise or ChannelNoise()
        self.hardware = hardware
        self.eve = eve
        self.seed = seed
        self.rng_kind = rng_kind
        self.max_attempts = max_attempts
        self.pools = {p: KeyPool(p) for p in PARTIES}
        self.history: list[KeyExchangeReport] = []
        self._aes_key: dict[str, bytes] = {}
        self._session = 0
        self._build_protocol()

    # -------------------------------------------------------------- config
    def _build_protocol(self) -> None:
        seed = None if self.seed is None else self.seed + 1000 * self._session
        self.protocol = BB84Protocol(
            self.config, noise=self.noise, hardware=self.hardware, eve=self.eve, seed=seed, rng_kind=self.rng_kind
        )

    def set_eve(self, fraction: float) -> None:
        self.eve = Eavesdropper(fraction) if fraction > 0 else None
        self._build_protocol()

    def set_noise(self, noise: ChannelNoise) -> None:
        self.noise = noise
        self._build_protocol()

    def set_mode(self, mode: str) -> None:
        if mode not in ("otp", "aes"):
            raise ValueError("mode must be 'otp' or 'aes'")
        self.mode = mode

    # -------------------------------------------------------------- keys
    def establish_key(self, n_qubits: int | None = None) -> KeyExchangeReport:
        """Run one BB84 exchange and append the result to both pools."""
        self._session += 1
        self._build_protocol()
        rep = self.protocol.run(n_qubits)
        self.history.append(rep)
        if rep.aborted:
            raise EavesdropperDetected(rep)
        assert rep.keys_match, "post-processing produced different keys"
        self.pools["Alice"].add(rep.alice_key)
        self.pools["Bob"].add(rep.bob_key)
        return rep

    def ensure_key(self, bits_needed: int) -> list[KeyExchangeReport]:
        new: list[KeyExchangeReport] = []
        failures = 0
        while self.pools["Alice"].available() < bits_needed:
            try:
                new.append(self.establish_key())
            except EavesdropperDetected:
                failures += 1
                if failures >= self.max_attempts:
                    raise
        return new

    def _aes_keys(self) -> tuple[bytes, bytes]:
        """Session AES key, distilled from 256 fresh QKD bits on first use."""
        if "Alice" not in self._aes_key:
            self.ensure_key(AesGcm.KEY_BITS)
            ka = AesGcm.derive(self.pools["Alice"].consume(AesGcm.KEY_BITS))
            kb = AesGcm.derive(self.pools["Bob"].consume(AesGcm.KEY_BITS))
            self._aes_key = {"Alice": ka, "Bob": kb}
        return self._aes_key["Alice"], self._aes_key["Bob"]

    def rotate_aes_key(self) -> None:
        self._aes_key.clear()

    # -------------------------------------------------------------- messaging
    def send(self, sender: str, text: str) -> Delivery:
        if sender not in PARTIES:
            raise ValueError(f"sender must be one of {PARTIES}")
        receiver = PARTIES[1 - PARTIES.index(sender)]
        data = text.encode("utf-8")
        rekeyed: list[KeyExchangeReport] = []

        if self.mode == "otp":
            need = OneTimePad.key_bits_needed(data)
            rekeyed = self.ensure_key(need)
            k_s = self.pools[sender].consume(need)
            k_r = self.pools[receiver].consume(need)
            ct = OneTimePad.encrypt(data, k_s)
            plain = OneTimePad.decrypt(ct, k_r)
            used = need
        else:
            before = len(self.history)
            fresh = "Alice" not in self._aes_key
            ka, kb = self._aes_keys()
            rekeyed = self.history[before:]
            keys = {"Alice": ka, "Bob": kb}
            aad = f"{sender}->{receiver}".encode()
            ct = AesGcm.encrypt(data, keys[sender], aad)
            plain = AesGcm.decrypt(ct, keys[receiver], aad)
            used = AesGcm.KEY_BITS if fresh else 0

        eve_view = ct.data.decode("latin-1", errors="replace")
        eve_view = "".join(ch if ch.isprintable() else "." for ch in eve_view)
        return Delivery(sender, receiver, text, ct, plain.decode("utf-8", errors="replace"), used, rekeyed, eve_view)

    # -------------------------------------------------------------- stats
    def stats(self) -> dict:
        ok = [r for r in self.history if not r.aborted]
        return {
            "exchanges": len(self.history),
            "aborted": sum(r.aborted for r in self.history),
            "qubits_sent": sum(r.n_raw for r in self.history),
            "secret_bits_generated": sum(r.final_length for r in ok),
            "mean_qber": float(np.mean([r.qber_estimate for r in self.history])) if self.history else 0.0,
            "pool_alice": self.pools["Alice"].available(),
            "pool_bob": self.pools["Bob"].available(),
            "bits_consumed": self.pools["Alice"].total_consumed,
        }
