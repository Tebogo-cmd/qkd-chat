"""Quantum simulation layer for BB84 (the centre block of the conceptual design).

Responsibilities
----------------
1. Generate Alice's random bits/bases and Bob's random bases (optionally with a
   quantum random number generator built from Hadamard + measure circuits).
2. Build the BB84 quantum circuits:

       Alice  :  |0> --[X if bit=1]--[H if basis=X]--
       Channel:  --[id]--  (noise is attached to this gate)
       Eve    :  --[H if e_basis=X]--[M -> eve]--[H if e_basis=X]--   (optional)
       Bob    :  --[H if b_basis=X]--[M -> bob]

   Eve's measurement collapses the qubit, so the circuit physically models an
   *intercept-resend* attack: Bob receives the state Eve measured.
3. Run the circuits on Qiskit Aer (ideal, parameterised noise, or a noise
   snapshot of a real IBM device) and return the measurement records.

Every qubit in BB84 is independent, so qubits are packed side by side into
"wide" circuits (``chunk_size`` qubits each, ``shots=1``). Because the circuits
are Clifford (X, H, measure) the stabilizer simulator handles hundreds of
qubits per circuit in milliseconds.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np
from qiskit import ClassicalRegister, QuantumCircuit, QuantumRegister, transpile
from qiskit_aer import AerSimulator

from .noise import ChannelNoise, hardware_backend

Z_BASIS = 0  # rectilinear  (+): |0>, |1>
X_BASIS = 1  # diagonal     (x): |+>, |->

BASIS_SYMBOL = {Z_BASIS: "+", X_BASIS: "x"}
STATE_SYMBOL = {(0, Z_BASIS): "|0>", (1, Z_BASIS): "|1>", (0, X_BASIS): "|+>", (1, X_BASIS): "|->"}


# --------------------------------------------------------------------------- #
# Randomness
# --------------------------------------------------------------------------- #
class RandomSource:
    """Source of random bits for Alice, Bob and Eve.

    ``kind="pseudo"``  - NumPy PCG64 generator (seedable -> reproducible experiments).
    ``kind="quantum"`` - a Qiskit QRNG: each bit is the outcome of H|0> measured
                         in Z. On a simulator this is still pseudo-random
                         underneath. Run on quantum hardware it could serve as a
                         QRNG, but that was not done in this project, and every
                         reported experiment used the seeded "pseudo" source.
    """

    def __init__(self, kind: str = "pseudo", seed: int | None = None, chunk: int = 256):
        if kind not in ("pseudo", "quantum"):
            raise ValueError("kind must be 'pseudo' or 'quantum'")
        self.kind = kind
        self.rng = np.random.default_rng(seed)
        self._seed = seed
        self._chunk = chunk
        self._sim = AerSimulator(method="stabilizer") if kind == "quantum" else None

    def bits(self, n: int) -> np.ndarray:
        if self.kind == "pseudo":
            return self.rng.integers(0, 2, size=n, dtype=np.uint8)
        out: list[np.ndarray] = []
        remaining = n
        while remaining > 0:
            k = min(self._chunk, remaining)
            qc = QuantumCircuit(k, k)
            qc.h(range(k))
            qc.measure(range(k), range(k))
            seed = None if self._seed is None else int(self.rng.integers(0, 2**31 - 1))
            mem = self._sim.run(qc, shots=1, memory=True, seed_simulator=seed).result().get_memory()[0]
            out.append(np.array([int(c) for c in reversed(mem)], dtype=np.uint8))
            remaining -= k
        return np.concatenate(out)[:n]

    def choice(self, n: int, size: int) -> np.ndarray:
        """Random sample of ``size`` distinct indices out of ``n`` (used for public sampling)."""
        return np.sort(self.rng.choice(n, size=size, replace=False))

    def permutation(self, n: int) -> np.ndarray:
        return self.rng.permutation(n)

    def integer(self, high: int) -> int:
        return int(self.rng.integers(0, high))


# --------------------------------------------------------------------------- #
# Eavesdropper
# --------------------------------------------------------------------------- #
@dataclass
class Eavesdropper:
    """Intercept-resend attacker.

    Eve intercepts each qubit with probability ``intercept_fraction``, measures
    it in a randomly chosen basis and forwards the collapsed state to Bob.
    For a full attack (fraction = 1) theory predicts QBER = 25 %.
    """

    intercept_fraction: float = 1.0

    def __post_init__(self):
        if not 0.0 <= self.intercept_fraction <= 1.0:
            raise ValueError("intercept_fraction must be in [0, 1]")

    @property
    def expected_qber(self) -> float:
        return 0.25 * self.intercept_fraction


# --------------------------------------------------------------------------- #
# Circuit construction
# --------------------------------------------------------------------------- #
def encode_qubit(qc: QuantumCircuit, q, bit: int, basis: int) -> None:
    """Alice: prepare |0>,|1> (Z basis) or |+>,|-> (X basis)."""
    if bit:
        qc.x(q)
    if basis == X_BASIS:
        qc.h(q)


def measure_qubit(qc: QuantumCircuit, q, clbit, basis: int) -> None:
    """Measure in the Z basis, or in the X basis by rotating with H first."""
    if basis == X_BASIS:
        qc.h(q)
    qc.measure(q, clbit)


def build_bb84_circuit(
    alice_bits,
    alice_bases,
    bob_bases,
    eve_mask=None,
    eve_bases=None,
    barriers: bool = True,
    eve_register: bool = True,
) -> QuantumCircuit:
    """Build one BB84 circuit carrying ``len(alice_bits)`` independent qubits.

    ``eve_register=False`` omits Eve's classical register when nobody
    eavesdrops, so the IBM Runtime path never carries an unused register.
    (Removing it did NOT fix the earlier SamplerV2 local-mode error; that was
    caused by the default simulator method and fixed by using MPS - see ibm.py.)
    """
    n = len(alice_bits)
    qr = QuantumRegister(n, "q")
    bob_c = ClassicalRegister(n, "bob")
    has_eve = eve_mask is not None and bool(np.any(eve_mask))
    if eve_register or has_eve:
        eve_c = ClassicalRegister(n, "eve")
        qc = QuantumCircuit(qr, eve_c, bob_c, name="BB84")
    else:
        qc = QuantumCircuit(qr, bob_c, name="BB84")

    # 1. Alice prepares
    for i in range(n):
        encode_qubit(qc, qr[i], int(alice_bits[i]), int(alice_bases[i]))
    if barriers:
        qc.barrier(label="Alice -> channel")

    # 2. Quantum channel (identity gate; noise models attach errors here)
    for i in range(n):
        qc.id(qr[i])

    # 3. Optional intercept-resend
    if has_eve:
        if barriers:
            qc.barrier(label="Eve")
        for i in np.flatnonzero(eve_mask):
            b = int(eve_bases[i])
            measure_qubit(qc, qr[i], eve_c[i], b)
            if b == X_BASIS:  # re-prepare the state in Eve's basis
                qc.h(qr[i])

    # 4. Bob measures
    if barriers:
        qc.barrier(label="Bob")
    for i in range(n):
        measure_qubit(qc, qr[i], bob_c[i], int(bob_bases[i]))
    return qc


def _parse_memory(mem: str, n: int) -> tuple[np.ndarray, np.ndarray]:
    """Split an Aer memory string into (eve_bits, bob_bits).

    Aer prints registers in reverse creation order separated by spaces, and
    each register little-endian (bit 0 right-most).
    """
    regs = mem.split(" ")[::-1]  # -> [eve, bob]
    eve = np.fromiter((int(c) for c in reversed(regs[0])), dtype=np.uint8, count=n)
    bob = np.fromiter((int(c) for c in reversed(regs[1])), dtype=np.uint8, count=n)
    return eve, bob


# --------------------------------------------------------------------------- #
# Simulator
# --------------------------------------------------------------------------- #
@dataclass
class QuantumTransmission:
    """Everything that happened on the quantum channel for one key exchange."""

    alice_bits: np.ndarray
    alice_bases: np.ndarray
    bob_bases: np.ndarray
    bob_results: np.ndarray
    eve_mask: np.ndarray
    eve_bases: np.ndarray
    eve_results: np.ndarray
    n_circuits: int
    backend_name: str
    timings: dict = field(default_factory=dict)

    @property
    def n_qubits(self) -> int:
        return len(self.alice_bits)


class QuantumChannelSimulator:
    """Runs BB84 transmissions on a Qiskit Aer backend.

    Args:
        noise: parameterised ``ChannelNoise`` (ignored if ``hardware`` is set).
        hardware: name of an IBM fake backend (e.g. ``"fake_torino"``) whose
            calibrated noise is used instead. Circuits are transpiled to that
            device's native gates (rz, sx, x, cz) before simulation.
        chunk_size: number of qubits packed into one circuit.
        seed: simulator seed for reproducibility.
    """

    def __init__(
        self,
        noise: ChannelNoise | None = None,
        hardware: str | None = None,
        chunk_size: int = 256,
        seed: int | None = None,
    ):
        self.noise = noise or ChannelNoise()
        self.hardware = hardware
        self.seed = seed
        self._rng = np.random.default_rng(seed)
        if hardware:
            self._device = hardware_backend(hardware)
            # Product states only -> MPS handles the non-Clifford noise efficiently.
            self.backend = AerSimulator.from_backend(self._device, method="matrix_product_state")
            self.chunk_size = min(chunk_size, self._device.num_qubits)
            self.backend_name = f"aer_mps+{hardware}_noise"
        else:
            self._device = None
            nm = self.noise.to_noise_model()
            self.backend = AerSimulator(method="stabilizer", noise_model=nm)
            self.chunk_size = chunk_size
            self.backend_name = "aer_stabilizer" + ("" if nm is None else "+noise")

    def transmit(
        self,
        alice_bits: np.ndarray,
        alice_bases: np.ndarray,
        bob_bases: np.ndarray,
        eve: Eavesdropper | None = None,
        rand: RandomSource | None = None,
    ) -> QuantumTransmission:
        rand = rand or RandomSource(seed=None if self.seed is None else self.seed + 1)
        n = len(alice_bits)
        if eve is not None and eve.intercept_fraction > 0:
            eve_mask = (rand.rng.random(n) < eve.intercept_fraction).astype(np.uint8)
            eve_bases = rand.bits(n)
        else:
            eve_mask = np.zeros(n, dtype=np.uint8)
            eve_bases = np.zeros(n, dtype=np.uint8)

        t0 = time.perf_counter()
        circuits, spans = [], []
        for start in range(0, n, self.chunk_size):
            stop = min(start + self.chunk_size, n)
            sl = slice(start, stop)
            qc = build_bb84_circuit(
                alice_bits[sl], alice_bases[sl], bob_bases[sl], eve_mask[sl], eve_bases[sl], barriers=False
            )
            circuits.append(qc)
            spans.append((start, stop))
        t_build = time.perf_counter() - t0

        t0 = time.perf_counter()
        if self._device is not None:
            circuits = transpile(circuits, self._device, optimization_level=0, seed_transpiler=self.seed)
        t_transpile = time.perf_counter() - t0

        t0 = time.perf_counter()
        seed = None if self.seed is None else int(self._rng.integers(0, 2**31 - 1))
        result = self.backend.run(circuits, shots=1, memory=True, seed_simulator=seed).result()
        t_sim = time.perf_counter() - t0

        bob_results = np.zeros(n, dtype=np.uint8)
        eve_results = np.zeros(n, dtype=np.uint8)
        for idx, (start, stop) in enumerate(spans):
            mem = result.get_memory(idx)[0]
            e, b = _parse_memory(mem, stop - start)
            bob_results[start:stop] = b
            eve_results[start:stop] = e

        return QuantumTransmission(
            alice_bits=alice_bits,
            alice_bases=alice_bases,
            bob_bases=bob_bases,
            bob_results=bob_results,
            eve_mask=eve_mask,
            eve_bases=eve_bases,
            eve_results=eve_results,
            n_circuits=len(circuits),
            backend_name=self.backend_name,
            timings={"build_s": t_build, "transpile_s": t_transpile, "simulate_s": t_sim},
        )


def demo_circuit(n: int = 6, with_eve: bool = False, seed: int | None = 7) -> tuple[QuantumCircuit, dict]:
    """Small, labelled BB84 circuit for display in the CLI / notebooks."""
    rand = RandomSource(seed=seed)
    a_bits, a_bases, b_bases = rand.bits(n), rand.bits(n), rand.bits(n)
    eve_mask = np.ones(n, dtype=np.uint8) if with_eve else None
    e_bases = rand.bits(n) if with_eve else None
    qc = build_bb84_circuit(a_bits, a_bases, b_bases, eve_mask, e_bases, barriers=True)
    info = {"alice_bits": a_bits, "alice_bases": a_bases, "bob_bases": b_bases, "eve_bases": e_bases}
    return qc, info
