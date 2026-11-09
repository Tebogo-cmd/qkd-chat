"""Per-party store of QKD key material ("Shared secret key" blocks in Figure 6)."""

from __future__ import annotations

import numpy as np


class KeyExhausted(RuntimeError):
    pass


class KeyPool:
    def __init__(self, owner: str):
        self.owner = owner
        self._bits = np.zeros(0, dtype=np.uint8)
        self.total_added = 0
        self.total_consumed = 0

    def add(self, bits: np.ndarray) -> None:
        self._bits = np.concatenate([self._bits, bits.astype(np.uint8)])
        self.total_added += len(bits)

    def available(self) -> int:
        return len(self._bits)

    def peek(self, n: int) -> np.ndarray:
        return self._bits[:n].copy()

    def consume(self, n: int) -> np.ndarray:
        """Remove and return the next ``n`` bits. Key bits are never reused."""
        if n > len(self._bits):
            raise KeyExhausted(f"{self.owner} needs {n} bits but only {len(self._bits)} are available")
        out, self._bits = self._bits[:n].copy(), self._bits[n:]
        self.total_consumed += n
        return out

    def fingerprint(self, n: int = 32) -> str:
        """Short hex preview of the head of the pool (for display only)."""
        head = self._bits[:n]
        if len(head) == 0:
            return "-"
        pad = (-len(head)) % 8
        return np.packbits(np.concatenate([head, np.zeros(pad, np.uint8)])).tobytes().hex()
