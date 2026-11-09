"""BB84 protocol orchestration - ties the quantum and classical layers together.

    Alice                       quantum channel (Aer)                     Bob
    random bits + bases  ---->  |psi_i>  ---- [Eve?] ---->  random bases, measure
                                 classical channel (public, authenticated)
    announce bases       <--->  sifting
    reveal sample        <--->  QBER estimate  ->  abort if QBER > 11 %
    Cascade parities     <--->  error correction
    hash tag             <--->  verification
    Toeplitz seed        ---->  privacy amplification  ->  identical secret keys
"""

from __future__ import annotations

import os
import time
from dataclasses import asdict, dataclass, field

import numpy as np

from . import postprocessing as pp
from .channel import ClassicalChannel
from .noise import ChannelNoise
from .quantum import Eavesdropper, QuantumChannelSimulator, QuantumTransmission, RandomSource


@dataclass
class BB84Config:
    n_qubits: int = 2048
    sample_fraction: float = 0.25
    abort_threshold: float = pp.QBER_ABORT_THRESHOLD
    cascade_passes: int = 4
    reconciliation_rounds: int = 3   # extra Cascade rounds if verification fails
    security_margin: int = 16
    finite_key: bool = False          # use Hoeffding upper bound on QBER for PA
    error_correction: bool = True
    privacy_amplification: bool = True
    chunk_size: int = 256


@dataclass
class KeyExchangeReport:
    # sizes
    n_raw: int = 0
    n_sifted: int = 0
    n_sample: int = 0
    n_reconciled: int = 0
    final_length: int = 0
    # error rates
    qber_estimate: float = 0.0
    qber_true: float = 0.0            # only observable in simulation
    qber_used_for_pa: float = 0.0
    # error correction
    errors_corrected: int = 0
    residual_errors: int = 0
    leaked_ec: int = 0
    cascade_first_block: int = 0
    reconciliation_rounds: int = 0
    verified: bool = False
    # eavesdropper (simulation ground truth)
    eve_intercepted: int = 0
    eve_known_fraction_before_pa: float = 0.0
    eve_agreement_before_pa: float = 0.0
    eve_agreement_after_pa: float = float("nan")
    # outcome
    aborted: bool = False
    abort_reason: str = ""
    backend: str = ""
    timings: dict = field(default_factory=dict)
    public_channel: dict = field(default_factory=dict)
    # keys (not serialised by to_dict)
    alice_key: np.ndarray | None = field(default=None, repr=False)
    bob_key: np.ndarray | None = field(default=None, repr=False)

    @property
    def sift_ratio(self) -> float:
        return self.n_sifted / self.n_raw if self.n_raw else 0.0

    @property
    def key_rate(self) -> float:
        """Secret bits per transmitted qubit."""
        return self.final_length / self.n_raw if self.n_raw else 0.0

    @property
    def keys_match(self) -> bool:
        return (
            self.alice_key is not None
            and self.bob_key is not None
            and len(self.alice_key) == len(self.bob_key)
            and bool(np.array_equal(self.alice_key, self.bob_key))
        )

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("alice_key")
        d.pop("bob_key")
        d["sift_ratio"] = self.sift_ratio
        d["key_rate"] = self.key_rate
        d["keys_match"] = self.keys_match
        return d


class BB84Protocol:
    def __init__(
        self,
        config: BB84Config | None = None,
        noise: ChannelNoise | None = None,
        hardware: str | None = None,
        eve: Eavesdropper | None = None,
        seed: int | None = None,
        rng_kind: str = "pseudo",
    ):
        self.config = config or BB84Config()
        self.eve = eve
        self.seed = seed
        self.alice_rng = RandomSource(rng_kind, seed=None if seed is None else seed)
        self.bob_rng = RandomSource(rng_kind, seed=None if seed is None else seed + 1)
        self.eve_rng = RandomSource("pseudo", seed=None if seed is None else seed + 2)
        self.public_rng = RandomSource("pseudo", seed=None if seed is None else seed + 3)
        self.simulator = QuantumChannelSimulator(
            noise=noise, hardware=hardware, chunk_size=self.config.chunk_size, seed=seed
        )
        self.channel = ClassicalChannel()
        self.last_transmission: QuantumTransmission | None = None

    # ------------------------------------------------------------------ #
    def run(self, n_qubits: int | None = None) -> KeyExchangeReport:
        cfg = self.config
        n = n_qubits or cfg.n_qubits
        rep = KeyExchangeReport(n_raw=n, backend=self.simulator.backend_name)
        self.channel.clear()
        t = {}

        # --- Quantum phase ------------------------------------------------
        t0 = time.perf_counter()
        a_bits, a_bases = self.alice_rng.bits(n), self.alice_rng.bits(n)
        b_bases = self.bob_rng.bits(n)
        t["random_s"] = time.perf_counter() - t0
        tx = self.simulator.transmit(a_bits, a_bases, b_bases, eve=self.eve, rand=self.eve_rng)
        self.last_transmission = tx
        t.update(tx.timings)
        rep.eve_intercepted = int(tx.eve_mask.sum())

        # --- (1) Sifting --------------------------------------------------
        t0 = time.perf_counter()
        self.channel.send("Bob", "bases", b_bases, n)
        self.channel.send("Alice", "bases", a_bases, n)
        keep = pp.sift(a_bases, b_bases)
        alice_sift = a_bits[keep]
        bob_sift = tx.bob_results[keep]
        rep.n_sifted = len(keep)
        t["sift_s"] = time.perf_counter() - t0

        # --- (2) Parameter estimation ------------------------------------
        t0 = time.perf_counter()
        n_sample = int(round(cfg.sample_fraction * rep.n_sifted))
        sample = self.public_rng.choice(rep.n_sifted, n_sample) if n_sample else np.zeros(0, int)
        self.channel.send("Alice", "sample_positions", sample, 0)
        self.channel.send("Alice", "sample_values", alice_sift[sample], n_sample)
        self.channel.send("Bob", "sample_values", bob_sift[sample], n_sample)
        rep.n_sample = n_sample
        rep.qber_estimate = pp.estimate_qber(alice_sift, bob_sift, sample)
        rest = np.setdiff1d(np.arange(rep.n_sifted), sample)
        alice_key, bob_key = alice_sift[rest], bob_sift[rest]
        rep.n_reconciled = len(rest)
        rep.qber_true = float(np.mean(alice_key != bob_key)) if len(rest) else 0.0
        t["estimate_s"] = time.perf_counter() - t0

        # Eve's knowledge of the remaining key (ground truth, simulation only)
        kept = keep[rest]
        eve_bits = tx.eve_results[kept]
        eve_guess = np.where(tx.eve_mask[kept] == 1, eve_bits, self.eve_rng.bits(len(kept)))
        if len(kept):
            exact = (tx.eve_mask[kept] == 1) & (tx.eve_bases[kept] == a_bases[kept])
            rep.eve_known_fraction_before_pa = float(exact.mean())
            rep.eve_agreement_before_pa = float(np.mean(eve_guess == alice_key))

        if rep.qber_estimate > cfg.abort_threshold:
            return self._abort(
                rep, t, f"QBER {rep.qber_estimate:.1%} exceeds {cfg.abort_threshold:.0%} - eavesdropper suspected"
            )
        if rep.n_reconciled == 0:
            return self._abort(rep, t, "no key bits left after sampling")

        # --- (3) Error correction + (4) Verification ---------------------
        # If the sampled QBER under-estimates the real error rate (small samples),
        # Cascade's blocks are too large and errors survive. Verification then
        # fails, so we run extra Cascade rounds with a doubled error estimate.
        t0 = time.perf_counter()
        q_blocks = rep.qber_estimate
        verify_bits = 0
        for attempt in range(cfg.reconciliation_rounds):
            if cfg.error_correction:
                res = pp.cascade(alice_key, bob_key, q_blocks, self.channel, self.public_rng.rng, cfg.cascade_passes)
                bob_key = res.corrected
                rep.errors_corrected += res.errors_corrected
                rep.leaked_ec += res.leaked_bits
                rep.cascade_first_block = rep.cascade_first_block or res.first_block_size
            nonce = os.urandom(8)
            tag_a = self.channel.send(
                "Alice", "verify_tag", pp.verification_tag(alice_key, nonce), pp.VERIFY_TAG_BITS
            )
            verify_bits += pp.VERIFY_TAG_BITS
            rep.verified = pp.verification_tag(bob_key, nonce) == tag_a
            rep.reconciliation_rounds = attempt + 1
            if rep.verified or not cfg.error_correction:
                break
            q_blocks = max(2 * q_blocks, 0.02)
        rep.residual_errors = int(np.sum(alice_key != bob_key))
        t["error_correction_s"] = time.perf_counter() - t0
        if not rep.verified:
            return self._abort(rep, t, "keys differ after error correction (verification failed)")

        # --- (5) Privacy amplification -----------------------------------
        t0 = time.perf_counter()
        q_pa = (
            pp.qber_upper_bound(rep.qber_estimate, rep.n_sample) if cfg.finite_key else rep.qber_estimate
        )
        rep.qber_used_for_pa = q_pa
        if cfg.privacy_amplification:
            m = pp.final_key_length(
                rep.n_reconciled, q_pa, rep.leaked_ec + verify_bits, cfg.security_margin
            )
            if m == 0:
                return self._abort(rep, t, "privacy amplification leaves no secret bits (key too short / QBER too high)")
            seed_bits = self.public_rng.bits(rep.n_reconciled + m - 1)
            self.channel.send("Alice", "toeplitz_seed", seed_bits, 0)
            final_a = pp.toeplitz_hash(alice_key, m, seed_bits)
            final_b = pp.toeplitz_hash(bob_key, m, seed_bits)
            final_e = pp.toeplitz_hash(eve_guess, m, seed_bits)
            rep.eve_agreement_after_pa = float(np.mean(final_e == final_a))
        else:
            final_a, final_b = alice_key, bob_key
        t["privacy_amplification_s"] = time.perf_counter() - t0

        rep.final_length = len(final_a)
        rep.alice_key, rep.bob_key = final_a, final_b
        rep.timings = t
        rep.timings["total_s"] = sum(v for k, v in t.items() if k.endswith("_s"))
        rep.public_channel = self.channel.summary()
        return rep

    def _abort(self, rep: KeyExchangeReport, t: dict, reason: str) -> KeyExchangeReport:
        rep.aborted = True
        rep.abort_reason = reason
        rep.timings = t
        rep.timings["total_s"] = sum(v for k, v in t.items() if k.endswith("_s"))
        rep.public_channel = self.channel.summary()
        return rep
