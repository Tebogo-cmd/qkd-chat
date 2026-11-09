"""Run the BB84 quantum channel on IBM Quantum hardware via Qiskit Runtime.

``IBMRuntimeChannel`` is a drop-in replacement for ``QuantumChannelSimulator``:
it exposes the same ``transmit()`` method, so the classical post-processing,
key pools and chat are unchanged - only the quantum layer moves to a real QPU.

Requires an IBM Quantum account (free Open Plan is enough for a few thousand
qubits). Save your token once:

    from qiskit_ibm_runtime import QiskitRuntimeService
    QiskitRuntimeService.save_account(channel="ibm_quantum_platform", token="<API key>")

Without an account, ``backend="fake_torino"`` runs the identical code path
locally (Runtime "local mode") - useful for testing before spending QPU time.
"""

from __future__ import annotations

import time

import numpy as np
from qiskit import transpile

from .quantum import Eavesdropper, QuantumTransmission, RandomSource, _parse_memory, build_bb84_circuit


class IBMRuntimeChannel:
    def __init__(self, backend: str | None = None, chunk_size: int = 100, seed: int | None = None):
        from qiskit_ibm_runtime import SamplerV2

        if backend and backend.startswith("fake_"):
            from qiskit_aer import AerSimulator

            from .noise import hardware_backend

            self.backend = hardware_backend(backend)
            # Local mode: wide product-state circuits need the MPS method (statevector cannot hold 100 qubits).
            self.sampler = SamplerV2(mode=AerSimulator.from_backend(self.backend, method="matrix_product_state"))
        else:
            from qiskit_ibm_runtime import QiskitRuntimeService

            service = QiskitRuntimeService()
            self.backend = (
                service.backend(backend) if backend else service.least_busy(operational=True, simulator=False)
            )
            self.sampler = SamplerV2(mode=self.backend)
        self.chunk_size = min(chunk_size, self.backend.num_qubits)
        self.seed = seed
        self.backend_name = f"ibm:{self.backend.name}"
        self.last_job_id: str | None = None

    def transmit(
        self,
        alice_bits: np.ndarray,
        alice_bases: np.ndarray,
        bob_bases: np.ndarray,
        eve: Eavesdropper | None = None,
        rand: RandomSource | None = None,
    ) -> QuantumTransmission:
        rand = rand or RandomSource(seed=self.seed)
        n = len(alice_bits)
        if eve is not None and eve.intercept_fraction > 0:
            eve_mask = (rand.rng.random(n) < eve.intercept_fraction).astype(np.uint8)
            eve_bases = rand.bits(n)
        else:
            eve_mask = np.zeros(n, np.uint8)
            eve_bases = np.zeros(n, np.uint8)

        t0 = time.perf_counter()
        spans = [(s, min(s + self.chunk_size, n)) for s in range(0, n, self.chunk_size)]
        circuits = [
            build_bb84_circuit(alice_bits[a:b], alice_bases[a:b], bob_bases[a:b], eve_mask[a:b], eve_bases[a:b],
                               barriers=False, eve_register=False)
            for a, b in spans
        ]
        t_build = time.perf_counter() - t0
        t0 = time.perf_counter()
        isa = transpile(circuits, self.backend, optimization_level=1, seed_transpiler=self.seed)
        t_transpile = time.perf_counter() - t0

        t0 = time.perf_counter()
        job = self.sampler.run(isa, shots=1)
        self.last_job_id = job.job_id()
        result = job.result()
        t_run = time.perf_counter() - t0

        bob = np.zeros(n, np.uint8)
        eve_res = np.zeros(n, np.uint8)
        for (a, b), pub in zip(spans, result):
            bob_s = pub.data.bob.get_bitstrings()[0]
            eve_s = pub.data.eve.get_bitstrings()[0] if hasattr(pub.data, "eve") else "0" * (b - a)
            e, bb = _parse_memory(f"{bob_s} {eve_s}", b - a)
            bob[a:b], eve_res[a:b] = bb, e
        return QuantumTransmission(alice_bits, alice_bases, bob_bases, bob, eve_mask, eve_bases, eve_res,
                                   len(circuits), self.backend_name,
                                   {"build_s": t_build, "transpile_s": t_transpile, "simulate_s": t_run})
