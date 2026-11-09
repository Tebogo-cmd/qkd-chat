"""Selected quantum algorithms implemented in Qiskit (objective 3).

Each algorithm is run (a) on the ideal Aer simulator and (b) under the noise of
a real IBM device snapshot, and is compared with its classical counterpart by
the number of oracle queries needed.

    Algorithm          Concept demonstrated             Classical queries   Quantum queries
    Bell state         superposition + entanglement     -                   -
    Deutsch-Jozsa      interference, exponential sep.   2^(n-1) + 1 (worst) 1
    Bernstein-Vazirani phase kickback                   n                   1
    Grover search      amplitude amplification          ~N/2 (average)      ~(pi/4) sqrt(N)

    python algorithms/quantum_algorithms.py
"""

from __future__ import annotations

import csv
import math
import sys
from pathlib import Path

from qiskit import QuantumCircuit, transpile
from qiskit_aer import AerSimulator

ROOT = Path(__file__).resolve().parents[1]
SHOTS = 4096


# --------------------------------------------------------------------------- circuits
def bell_state() -> QuantumCircuit:
    qc = QuantumCircuit(2, 2, name="Bell")
    qc.h(0)
    qc.cx(0, 1)
    qc.measure([0, 1], [0, 1])
    return qc


def deutsch_jozsa(n: int, balanced: bool = True, mask: str | None = None) -> QuantumCircuit:
    """Decide whether f:{0,1}^n -> {0,1} is constant or balanced with ONE query.

    Balanced oracle: f(x) = x . mask (mod 2) for a non-zero mask.
    Output all-zeros  <=> constant.
    """
    qc = QuantumCircuit(n + 1, n, name=f"DJ-{'bal' if balanced else 'const'}")
    qc.x(n)
    qc.h(range(n + 1))
    qc.barrier()
    if balanced:
        mask = mask or "1" * n
        for i, b in enumerate(reversed(mask)):
            if b == "1":
                qc.cx(i, n)
    qc.barrier()
    qc.h(range(n))
    qc.measure(range(n), range(n))
    return qc


def bernstein_vazirani(secret: str) -> QuantumCircuit:
    """Recover a hidden n-bit string s from f(x) = s . x with ONE query."""
    n = len(secret)
    qc = QuantumCircuit(n + 1, n, name=f"BV-{secret}")
    qc.x(n)
    qc.h(range(n + 1))
    qc.barrier()
    for i, b in enumerate(reversed(secret)):
        if b == "1":
            qc.cx(i, n)
    qc.barrier()
    qc.h(range(n))
    qc.measure(range(n), range(n))
    return qc


def _phase_oracle(qc: QuantumCircuit, marked: str) -> None:
    n = len(marked)
    zeros = [i for i, b in enumerate(reversed(marked)) if b == "0"]
    if zeros:
        qc.x(zeros)
    qc.h(n - 1)
    qc.mcx(list(range(n - 1)), n - 1)
    qc.h(n - 1)
    if zeros:
        qc.x(zeros)


def _diffuser(qc: QuantumCircuit, n: int) -> None:
    qc.h(range(n))
    qc.x(range(n))
    qc.h(n - 1)
    qc.mcx(list(range(n - 1)), n - 1)
    qc.h(n - 1)
    qc.x(range(n))
    qc.h(range(n))


def grover(marked: str, iterations: int | None = None) -> QuantumCircuit:
    """Find the marked item among N = 2^n with ~ (pi/4) sqrt(N) oracle calls."""
    n = len(marked)
    k = iterations or max(1, math.floor(math.pi / 4 * math.sqrt(2**n)))
    qc = QuantumCircuit(n, n, name=f"Grover-{marked}")
    qc.h(range(n))
    for _ in range(k):
        _phase_oracle(qc, marked)
        _diffuser(qc, n)
    qc.measure(range(n), range(n))
    return qc


# --------------------------------------------------------------------------- experiments
CASES = [
    # (name, circuit, success predicate, classical worst-case queries, quantum queries)
    ("Bell state", bell_state(), lambda k: k in ("00", "11"), None, None),
    ("Deutsch-Jozsa n=3 (balanced)", deutsch_jozsa(3, True, "101"), lambda k: k != "000", 2**2 + 1, 1),
    ("Deutsch-Jozsa n=3 (constant)", deutsch_jozsa(3, False), lambda k: k == "000", 2**2 + 1, 1),
    ("Bernstein-Vazirani s=1011", bernstein_vazirani("1011"), lambda k: k == "1011", 4, 1),
    ("Grover n=2 (find 11)", grover("11"), lambda k: k == "11", 3, 1),
    ("Grover n=3 (find 101)", grover("101"), lambda k: k == "101", 7, 2),
    ("Grover n=4 (find 0110)", grover("0110"), lambda k: k == "0110", 15, 3),
]


def run_all(hardware: str = "fake_torino") -> list[dict]:
    from qiskit_ibm_runtime import fake_provider

    device = getattr(fake_provider, "".join(p.capitalize() for p in hardware.split("_")))()
    ideal = AerSimulator()
    noisy = AerSimulator.from_backend(device)
    rows = []
    for name, qc, ok, cq, qq in CASES:
        res_i = ideal.run(transpile(qc, ideal), shots=SHOTS, seed_simulator=1).result().get_counts()
        tqc = transpile(qc, device, optimization_level=3, seed_transpiler=1)
        res_n = noisy.run(tqc, shots=SHOTS, seed_simulator=1).result().get_counts()
        ops = tqc.count_ops()
        rows.append(
            {
                "algorithm": name,
                "qubits": qc.num_qubits,
                "logical_depth": qc.depth(),
                "hardware_depth": tqc.depth(),
                "hardware_2q_gates": int(ops.get("cz", 0) + ops.get("ecr", 0) + ops.get("cx", 0)),
                "success_ideal": sum(v for k, v in res_i.items() if ok(k)) / SHOTS,
                "success_noisy": sum(v for k, v in res_n.items() if ok(k)) / SHOTS,
                "classical_queries_worst": cq,
                "quantum_queries": qq,
                "top_ideal": max(res_i, key=res_i.get),
                "top_noisy": max(res_n, key=res_n.get),
            }
        )
    return rows


def main() -> None:
    sys.path.insert(0, str(ROOT / "experiments"))
    from plotstyle import SERIES, plt, style

    rows = run_all()
    out = ROOT / "results"
    out.mkdir(exist_ok=True)
    with open(out / "algorithms.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    for r in rows:
        print(f"{r['algorithm']:<32} depth {r['logical_depth']:>3} -> {r['hardware_depth']:>4} on HW  "
              f"2q={r['hardware_2q_gates']:>3}  ideal {r['success_ideal']:.3f}  noisy {r['success_noisy']:.3f}")

    style()
    fig, ax = plt.subplots(figsize=(8, 3.8))
    names = [r["algorithm"] for r in rows]
    y = range(len(rows))
    ax.barh([i - 0.18 for i in y], [r["success_ideal"] for r in rows], height=0.34, color=SERIES[0], label="Ideal simulator")
    ax.barh([i + 0.18 for i in y], [r["success_noisy"] for r in rows], height=0.34, color=SERIES[1], label="IBM Torino noise model")
    for i, r in enumerate(rows):
        ax.text(r["success_noisy"] + 0.01, i + 0.18, f"{r['success_noisy']:.2f}  (HW depth {r['hardware_depth']})",
                va="center", fontsize=7)
    ax.set_yticks(list(y), names, fontsize=8)
    ax.invert_yaxis()
    ax.set(xlim=(0, 1.25), xlabel="Probability of the correct answer", title="Selected algorithms: ideal vs. noisy hardware")
    ax.legend(fontsize=8, loc="lower right")
    fig.tight_layout()
    fig.savefig(out / "algorithms.png")
    print("saved results/algorithms.csv and results/algorithms.png")


if __name__ == "__main__":
    main()
