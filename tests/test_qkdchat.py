"""Structured tests for the BB84 implementation, key exchange and encryption workflow
(proposal section 5.5: "System functionality will be verified through structured testing")."""

import numpy as np
import pytest

from qkdchat import (
    AesGcm,
    BB84Config,
    BB84Protocol,
    ChannelNoise,
    Eavesdropper,
    EavesdropperDetected,
    OneTimePad,
    QuantumChannelSimulator,
    RandomSource,
    SecureLink,
    build_bb84_circuit,
)
from qkdchat import postprocessing as pp
from qkdchat.channel import ClassicalChannel
from qkdchat.keypool import KeyExhausted, KeyPool
from qkdchat.quantum import X_BASIS, Z_BASIS


# ----------------------------------------------------------------- quantum layer
@pytest.mark.parametrize("bit,basis", [(0, Z_BASIS), (1, Z_BASIS), (0, X_BASIS), (1, X_BASIS)])
def test_matching_basis_is_deterministic(bit, basis):
    """Each of the four BB84 states is measured correctly in its own basis."""
    n = 64
    sim = QuantumChannelSimulator(seed=1)
    tx = sim.transmit(np.full(n, bit, np.uint8), np.full(n, basis, np.uint8), np.full(n, basis, np.uint8))
    assert np.all(tx.bob_results == bit)


def test_mismatched_basis_is_random():
    n = 4000
    sim = QuantumChannelSimulator(seed=2)
    tx = sim.transmit(np.zeros(n, np.uint8), np.zeros(n, np.uint8), np.ones(n, np.uint8))
    assert 0.45 < tx.bob_results.mean() < 0.55


def test_circuit_structure():
    # Alice: X on q0, H on q0 (X basis). Bob: H on both (X basis). Channel: id on both.
    qc = build_bb84_circuit([1, 0], [X_BASIS, Z_BASIS], [X_BASIS, X_BASIS])
    ops = qc.count_ops()
    assert qc.num_qubits == 2 and ops["x"] == 1 and ops["h"] == 3 and ops["measure"] == 2 and ops["id"] == 2


def test_full_intercept_resend_gives_25_percent_qber():
    rand = RandomSource(seed=3)
    n = 8000
    a, ab, bb = rand.bits(n), rand.bits(n), rand.bits(n)
    tx = QuantumChannelSimulator(seed=3).transmit(a, ab, bb, eve=Eavesdropper(1.0), rand=rand)
    m = ab == bb
    assert abs(np.mean(tx.bob_results[m] != a[m]) - 0.25) < 0.03


def test_quantum_rng_is_balanced():
    bits = RandomSource("quantum", seed=4).bits(2000)
    assert set(np.unique(bits)) <= {0, 1}
    assert 0.45 < bits.mean() < 0.55


def test_noise_model_raises_qber_close_to_prediction():
    noise = ChannelNoise(channel_flip=0.1)
    rep = BB84Protocol(BB84Config(privacy_amplification=False), noise=noise, seed=5).run(8000)
    assert abs(rep.qber_true - noise.expected_qber()) < 0.015


# ----------------------------------------------------------------- post-processing
def test_sifting_keeps_matching_bases():
    assert list(pp.sift(np.array([0, 1, 1, 0]), np.array([0, 0, 1, 1]))) == [0, 2]


def test_binary_entropy():
    assert pp.binary_entropy(0) == 0 and pp.binary_entropy(0.5) == pytest.approx(1.0)
    assert pp.binary_entropy(0.11) == pytest.approx(0.4999, abs=1e-3)


@pytest.mark.parametrize("qber", [0.01, 0.03, 0.06, 0.1])
def test_cascade_corrects_errors(qber):
    rng = np.random.default_rng(7)
    n = 2000
    alice = rng.integers(0, 2, n, dtype=np.uint8)
    flips = rng.random(n) < qber
    bob = alice ^ flips.astype(np.uint8)
    res = pp.cascade(alice, bob, qber, ClassicalChannel(), rng)
    assert np.array_equal(res.corrected, alice)
    assert res.errors_corrected == flips.sum()
    # Cascade should leak within ~1.1-1.5x the Shannon limit n*h(Q)
    assert res.leaked_bits < 2.0 * n * pp.binary_entropy(qber) + 64


def test_toeplitz_hash_is_linear_and_deterministic():
    rng = np.random.default_rng(8)
    x, y = rng.integers(0, 2, 100, dtype=np.uint8), rng.integers(0, 2, 100, dtype=np.uint8)
    seed = rng.integers(0, 2, 100 + 40 - 1, dtype=np.uint8)
    hx, hy, hxy = (pp.toeplitz_hash(v, 40, seed) for v in (x, y, x ^ y))
    assert np.array_equal(hx ^ hy, hxy)
    assert np.array_equal(hx, pp.toeplitz_hash(x, 40, seed))


def test_detection_probability_formula():
    assert pp.detection_probability(1) == pytest.approx(0.25)
    assert pp.detection_probability(72) > 0.99999999


# ----------------------------------------------------------------- protocol
def test_clean_channel_produces_identical_keys():
    rep = BB84Protocol(seed=10).run(2048)
    assert not rep.aborted and rep.keys_match
    assert rep.qber_estimate == 0 and rep.final_length > 500
    assert 0.45 < rep.sift_ratio < 0.55


def test_eve_is_detected_and_key_discarded():
    rep = BB84Protocol(eve=Eavesdropper(1.0), seed=11).run(2048)
    assert rep.aborted and "eavesdropper" in rep.abort_reason
    assert rep.alice_key is None


def test_noisy_channel_still_yields_matching_keys():
    rep = BB84Protocol(noise=ChannelNoise(channel_flip=0.08), seed=12).run(4096)
    assert not rep.aborted and rep.keys_match and rep.errors_corrected > 0 and rep.residual_errors == 0


def test_privacy_amplification_removes_eves_partial_knowledge():
    rep = BB84Protocol(eve=Eavesdropper(0.2), seed=13).run(8192)
    assert not rep.aborted
    assert rep.eve_agreement_before_pa > 0.53          # Eve knows something ...
    assert abs(rep.eve_agreement_after_pa - 0.5) < 0.05  # ... which PA erases


# ----------------------------------------------------------------- crypto / link
def test_one_time_pad_roundtrip():
    key = np.random.default_rng(1).integers(0, 2, 800, dtype=np.uint8)
    ct = OneTimePad.encrypt(b"quantum secure", key)
    assert ct.data != b"quantum secure" and OneTimePad.decrypt(ct, key) == b"quantum secure"


def test_aes_gcm_roundtrip_and_tamper_detection():
    key = AesGcm.derive(np.random.default_rng(2).integers(0, 2, 256, dtype=np.uint8))
    ct = AesGcm.encrypt(b"hello", key, b"aad")
    assert AesGcm.decrypt(ct, key, b"aad") == b"hello"
    ct.data = bytes([ct.data[0] ^ 1]) + ct.data[1:]
    from cryptography.exceptions import InvalidTag

    with pytest.raises(InvalidTag):
        AesGcm.decrypt(ct, key, b"aad")


def test_key_pool_never_reuses_bits():
    pool = KeyPool("A")
    pool.add(np.array([1, 0, 1, 1], np.uint8))
    assert list(pool.consume(3)) == [1, 0, 1] and pool.available() == 1
    with pytest.raises(KeyExhausted):
        pool.consume(2)


@pytest.mark.parametrize("mode", ["otp", "aes"])
def test_secure_link_chat(mode):
    link = SecureLink(mode=mode, seed=20)
    for sender, text in [("Alice", "Hi Bob"), ("Bob", "Hi Alice - 你好 🔐"), ("Alice", "x" * 200)]:
        d = link.send(sender, text)
        assert d.ok and d.ciphertext.data != text.encode()
    assert link.pools["Alice"].available() == link.pools["Bob"].available()


def test_secure_link_refuses_to_send_under_attack():
    link = SecureLink(mode="otp", eve=Eavesdropper(1.0), seed=21, max_attempts=2)
    with pytest.raises(EavesdropperDetected):
        link.send("Alice", "secret")
    assert link.stats()["aborted"] == 2 and link.pools["Alice"].available() == 0
