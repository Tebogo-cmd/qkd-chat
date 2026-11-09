"""Classical post-processing for BB84.

Pipeline (all steps run over the authenticated public channel):

    raw key --(1) sifting--> sifted key --(2) parameter estimation (QBER)-->
    --(3) error correction (Cascade)--> --(4) verification (hash tag)-->
    --(5) privacy amplification (Toeplitz hashing)--> final secret key

References: Bennett & Brassard (1984); Brassard & Salvail (1993) for Cascade;
Carter & Wegman (1979) for universal hashing; Gottesman-Lo-Lutkenhaus-Preskill
(2004) for the asymptotic BB84 key-rate bound used in ``final_key_length``.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass

import numpy as np

from .channel import ClassicalChannel

QBER_ABORT_THRESHOLD = 0.11  # BB84 with one-way post-processing: no key above ~11 %


def binary_entropy(p: float) -> float:
    if p <= 0.0 or p >= 1.0:
        return 0.0
    return -p * math.log2(p) - (1 - p) * math.log2(1 - p)


# --------------------------------------------------------------------------- #
# (1) Sifting
# --------------------------------------------------------------------------- #
def sift(alice_bases: np.ndarray, bob_bases: np.ndarray) -> np.ndarray:
    """Indices where Alice and Bob happened to use the same basis (~50 %)."""
    return np.flatnonzero(alice_bases == bob_bases)


# --------------------------------------------------------------------------- #
# (2) Parameter estimation
# --------------------------------------------------------------------------- #
def estimate_qber(alice_key: np.ndarray, bob_key: np.ndarray, sample_idx: np.ndarray) -> float:
    if len(sample_idx) == 0:
        return 0.0
    return float(np.mean(alice_key[sample_idx] != bob_key[sample_idx]))


def qber_upper_bound(qber: float, n_sample: int, epsilon: float = 1e-6) -> float:
    """Hoeffding upper confidence bound on the true QBER (finite-key mode)."""
    if n_sample == 0:
        return 0.5
    return min(0.5, qber + math.sqrt(math.log(1 / epsilon) / (2 * n_sample)))


def detection_probability(n_checked: int, intercept_fraction: float = 1.0) -> float:
    """P(at least one error among n checked bits) under intercept-resend."""
    return 1.0 - (1.0 - 0.25 * intercept_fraction) ** n_checked


# --------------------------------------------------------------------------- #
# (3) Error correction - Cascade
# --------------------------------------------------------------------------- #
@dataclass
class CascadeResult:
    corrected: np.ndarray
    leaked_bits: int
    errors_corrected: int
    passes: int
    first_block_size: int


def cascade(
    alice_key: np.ndarray,
    bob_key: np.ndarray,
    qber: float,
    channel: ClassicalChannel,
    rng: np.random.Generator,
    passes: int = 4,
) -> CascadeResult:
    """Cascade reconciliation (Brassard & Salvail, 1993) with back-tracking.

    Bob corrects his key towards Alice's using only parity information that
    Alice discloses publicly. Every disclosed parity is one bit of information
    leaked to Eve and is counted so privacy amplification can remove it.
    """
    n = len(alice_key)
    bob = bob_key.copy()
    if n == 0:
        return CascadeResult(bob, 0, 0, 0, 0)

    q = max(qber, 1e-3)
    # Block size ~0.73/QBER, capped so even an error-free estimate still checks >= 8 blocks.
    k1 = int(max(4, min(max(4, n // 8), math.ceil(0.73 / q))))
    leaked = 0
    corrected = 0
    perms: list[np.ndarray] = []
    invs: list[np.ndarray] = []
    sizes: list[int] = []
    alice_parity: dict[tuple[int, int], int] = {}

    def par(key: np.ndarray, idxs: np.ndarray) -> int:
        return int(key[idxs].sum() & 1)

    def ask_alice(idxs: np.ndarray) -> int:
        nonlocal leaked
        leaked += 1
        return channel.send("Alice", "cascade_parity", par(alice_key, idxs), 1)

    def bisect(idxs: np.ndarray) -> int:
        """Binary search for one error inside a block with odd parity mismatch."""
        cur = idxs
        while len(cur) > 1:
            half = cur[: len(cur) // 2]
            if ask_alice(half) != par(bob, half):
                cur = half
            else:
                cur = cur[len(cur) // 2 :]
        return int(cur[0])

    def block(p: int, b: int) -> np.ndarray:
        k = sizes[p]
        return perms[p][b * k : (b + 1) * k]

    def block_of(p: int, orig: int) -> int:
        return int(invs[p][orig] // sizes[p])

    for p in range(passes):
        perm = np.arange(n) if p == 0 else rng.permutation(n)
        if p > 0:
            channel.send("Alice", "cascade_permutation_seed", None, 0)  # public, no key info
        inv = np.empty(n, dtype=np.int64)
        inv[perm] = np.arange(n)
        k = int(min(n, k1 * (2**p)))
        perms.append(perm)
        invs.append(inv)
        sizes.append(k)

        for b in range(math.ceil(n / k)):
            idxs = block(p, b)
            alice_parity[(p, b)] = ask_alice(idxs)
            if alice_parity[(p, b)] == par(bob, idxs):
                continue
            stack = [(p, b)]
            while stack:
                pp, bb = stack.pop()
                if (pp, bb) not in alice_parity:
                    continue  # not yet disclosed; will be checked later in its own pass
                idxs = block(pp, bb)
                if alice_parity[(pp, bb)] == par(bob, idxs):
                    continue
                pos = bisect(idxs)
                bob[pos] ^= 1
                corrected += 1
                # Cascade effect: flipping `pos` changes parity of its block in every other pass
                for qq in range(p + 1):
                    if qq != pp:
                        stack.append((qq, block_of(qq, pos)))

        if p > 0 and k >= n:
            break  # a single block covers everything - further passes add nothing
    return CascadeResult(bob, leaked, corrected, len(sizes), k1)


# --------------------------------------------------------------------------- #
# (4) Verification
# --------------------------------------------------------------------------- #
VERIFY_TAG_BITS = 64


def verification_tag(key: np.ndarray, nonce: bytes) -> bytes:
    h = hashlib.sha256(nonce + np.packbits(key).tobytes() + len(key).to_bytes(4, "big"))
    return h.digest()[: VERIFY_TAG_BITS // 8]


# --------------------------------------------------------------------------- #
# (5) Privacy amplification
# --------------------------------------------------------------------------- #
def final_key_length(
    n_reconciled: int,
    qber: float,
    leaked_ec: int,
    security_margin: int = 16,
) -> int:
    """Asymptotic BB84 secret length:  n[1 - h(Q)] - leak_EC - margin.

    The n*h(Q) term bounds what Eve learned on the quantum channel (phase
    errors ~ bit errors for BB84), leak_EC is what reconciliation disclosed,
    and the margin covers the verification tag / finite-size slack.
    """
    m = n_reconciled * (1 - binary_entropy(qber)) - leaked_ec - security_margin
    return max(0, int(math.floor(m)))


def toeplitz_hash(key: np.ndarray, out_len: int, seed_bits: np.ndarray) -> np.ndarray:
    """Multiply ``key`` by a random m x n Toeplitz matrix over GF(2).

    Toeplitz matrices form a 2-universal hash family, so by the leftover hash
    lemma the output is close to uniform from Eve's point of view.
    ``seed_bits`` (n + m - 1 random bits) is sent publicly.
    """
    n = len(key)
    if out_len == 0 or n == 0:
        return np.zeros(0, dtype=np.uint8)
    if len(seed_bits) != n + out_len - 1:
        raise ValueError("seed must contain n + m - 1 bits")
    idx = np.arange(out_len)[:, None] - np.arange(n)[None, :] + (n - 1)
    T = seed_bits[idx].astype(np.uint8)
    return ((T.astype(np.int64) @ key.astype(np.int64)) & 1).astype(np.uint8)
