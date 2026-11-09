"""Noise models for the simulated quantum channel.

Two kinds of noise are supported:

* ``ChannelNoise`` - a simple, parameterised model built from Pauli channels.
  It is fully Clifford-compatible, so Aer's very fast *stabilizer* method can
  still be used. Useful for controlled experiments (QBER vs. noise).
* ``hardware_backend(name)`` - a snapshot of a real IBM Quantum device's noise
  (T1/T2, gate and readout errors) taken from ``qiskit-ibm-runtime``'s fake
  provider. This gives "realistic hardware" results without an IBM account.
"""

from __future__ import annotations

from dataclasses import dataclass

from qiskit_aer.noise import NoiseModel, ReadoutError, depolarizing_error, pauli_error


@dataclass(frozen=True)
class ChannelNoise:
    """Parameterised noise for the BB84 quantum channel.

    Attributes:
        channel_flip: probability that the fibre/free-space channel applies a
            random Pauli error (depolarising channel) to a qubit in flight.
        gate_error: depolarising error probability on Alice's/Bob's X and H gates.
        readout_error: probability that Bob's detector reports the wrong bit.
    """

    channel_flip: float = 0.0
    gate_error: float = 0.0
    readout_error: float = 0.0

    @property
    def is_ideal(self) -> bool:
        return self.channel_flip == 0 and self.gate_error == 0 and self.readout_error == 0

    def expected_qber(self) -> float:
        """First-order analytic estimate of the QBER this noise produces.

        Qiskit's depolarising channel E(rho) = (1-p) rho + p I/2 flips the
        measured bit with probability p/2 in either basis. Readout errors flip
        the result directly. Gate errors are approximated as one noisy gate
        per sifted qubit, so this is an estimate, not an exact value.
        """
        p_chan = self.channel_flip / 2
        p_gate = self.gate_error / 2
        p_read = self.readout_error
        p = p_chan
        for q in (p_gate, p_read):
            p = p * (1 - q) + (1 - p) * q
        return p

    def to_noise_model(self) -> NoiseModel | None:
        if self.is_ideal:
            return None
        nm = NoiseModel(basis_gates=["x", "h", "id", "measure"])
        if self.channel_flip > 0:
            nm.add_all_qubit_quantum_error(depolarizing_error(self.channel_flip, 1), ["id"])
        if self.gate_error > 0:
            nm.add_all_qubit_quantum_error(depolarizing_error(self.gate_error, 1), ["x", "h"])
        if self.readout_error > 0:
            p = self.readout_error
            nm.add_all_qubit_readout_error(ReadoutError([[1 - p, p], [p, 1 - p]]))
        return nm


def bit_flip_channel(p: float) -> NoiseModel:
    """Pure bit-flip channel (X error with probability p) - handy for teaching."""
    nm = NoiseModel(basis_gates=["x", "h", "id", "measure"])
    nm.add_all_qubit_quantum_error(pauli_error([("X", p), ("I", 1 - p)]), ["id"])
    return nm


HARDWARE_BACKENDS = ("fake_torino", "fake_brisbane", "fake_sherbrooke", "fake_kyiv")


def hardware_backend(name: str = "fake_torino"):
    """Return a fake IBM backend whose noise mirrors a real device snapshot."""
    from qiskit_ibm_runtime import fake_provider

    class_name = "".join(part.capitalize() for part in name.split("_"))
    try:
        return getattr(fake_provider, class_name)()
    except AttributeError as exc:  # pragma: no cover - depends on installed version
        raise ValueError(f"Unknown fake backend '{name}'. Try one of {HARDWARE_BACKENDS}.") from exc
