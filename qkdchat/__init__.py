"""qkdchat - a hybrid quantum-classical BB84 QKD chat application built with Qiskit.

Honours research artefact: "An Exploratory Analysis of Quantum Computing
Programming Using Qiskit" - T. Diraditsile, CPUT, 2026.
"""

from .crypto import AesGcm, OneTimePad
from .link import Delivery, EavesdropperDetected, SecureLink
from .noise import ChannelNoise
from .protocol import BB84Config, BB84Protocol, KeyExchangeReport
from .quantum import Eavesdropper, QuantumChannelSimulator, RandomSource, build_bb84_circuit

__version__ = "1.0.0"

__all__ = [
    "AesGcm",
    "BB84Config",
    "BB84Protocol",
    "ChannelNoise",
    "Delivery",
    "Eavesdropper",
    "EavesdropperDetected",
    "KeyExchangeReport",
    "OneTimePad",
    "QuantumChannelSimulator",
    "RandomSource",
    "SecureLink",
    "build_bb84_circuit",
]
