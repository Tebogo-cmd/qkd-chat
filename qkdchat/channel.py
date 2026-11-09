"""Authenticated classical (public) channel.

Everything sent here is visible to Eve - basis announcements, sampled bits,
Cascade parities, Toeplitz seeds and ciphertexts. The channel keeps a log so
the application can show *exactly* what an eavesdropper learns, and counts the
number of key-related bits disclosed (needed for privacy amplification).

BB84 assumes this channel is **authenticated** (Eve can read but not modify).
In this proof-of-concept that assumption is modelled, not implemented; see
docs/ARCHITECTURE.md -> "Limitations".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PublicMessage:
    sender: str
    kind: str
    payload: Any
    size_bits: int


@dataclass
class ClassicalChannel:
    log: list[PublicMessage] = field(default_factory=list)

    def send(self, sender: str, kind: str, payload: Any, size_bits: int = 0) -> Any:
        self.log.append(PublicMessage(sender, kind, payload, size_bits))
        return payload

    def bits_sent(self, kinds: tuple[str, ...] | None = None) -> int:
        return sum(m.size_bits for m in self.log if kinds is None or m.kind in kinds)

    def count(self, kind: str) -> int:
        return sum(1 for m in self.log if m.kind == kind)

    def clear(self) -> None:
        self.log.clear()

    def summary(self) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        for m in self.log:
            d = out.setdefault(m.kind, {"messages": 0, "bits": 0})
            d["messages"] += 1
            d["bits"] += m.size_bits
        return out
