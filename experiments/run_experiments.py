"""Experimental evaluation of the BB84 QKD application (proposal section 5.5).

Runs every experiment, writes raw data to ``results/*.csv``, figures to
``results/*.png`` and a machine-readable ``results/summary.json``.

    python experiments/run_experiments.py            # full run (~3-5 min)
    python experiments/run_experiments.py --quick    # fewer trials (~1 min)
    python experiments/run_experiments.py --only e1 e4

Experiments
    E1  QBER and detection rate vs. Eve's intercept fraction
    E2  QBER and secret-key rate vs. channel noise
    E3  Probability of detecting Eve vs. number of sampled bits
    E4  Simulation time vs. key size, and simulator method comparison
    E5  Realistic IBM-device noise (fake backends) - QBER and key rate
    E6  Reconciliation efficiency of Cascade vs. QBER
    E7  Classical baseline (X25519 Diffie-Hellman) vs. BB84 - time per 256-bit key
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "experiments"))

from plotstyle import INK2, SERIES, plt, style  # noqa: E402

from qkdchat import BB84Protocol, ChannelNoise, Eavesdropper  # noqa: E402
from qkdchat import postprocessing as pp  # noqa: E402
from qkdchat.channel import ClassicalChannel  # noqa: E402
from qkdchat.quantum import QuantumChannelSimulator, RandomSource  # noqa: E402

OUT = ROOT / "results"
SUMMARY: dict = {}


def save_csv(name: str, rows: list[dict]) -> None:
    OUT.mkdir(exist_ok=True)
    with open(OUT / f"{name}.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def mean_ci(xs) -> tuple[float, float]:
    xs = np.asarray(xs, dtype=float)
    xs = xs[~np.isnan(xs)]
    if len(xs) < 2:
        return (float(xs.mean()) if len(xs) else float("nan")), 0.0
    return float(xs.mean()), float(1.96 * xs.std(ddof=1) / math.sqrt(len(xs)))


# --------------------------------------------------------------------------- E1
def e1_eve(trials: int, n: int) -> None:
    print("E1  QBER vs Eve intercept fraction")
    rows = []
    fracs = np.round(np.linspace(0, 1, 11), 2)
    for f in fracs:
        for t in range(trials):
            eve = Eavesdropper(float(f)) if f > 0 else None
            r = BB84Protocol(eve=eve, seed=1000 + 37 * t + int(100 * f)).run(n)
            rows.append({"intercept_fraction": f, "trial": t, "qber_estimate": r.qber_estimate,
                         "qber_true": r.qber_true, "aborted": int(r.aborted), "final_length": r.final_length,
                         "eve_agreement_before_pa": r.eve_agreement_before_pa,
                         "eve_agreement_after_pa": r.eve_agreement_after_pa})
    save_csv("e1_qber_vs_eve", rows)

    agg = []
    for f in fracs:
        sub = [r for r in rows if r["intercept_fraction"] == f]
        q, ci = mean_ci([r["qber_true"] for r in sub])
        agg.append({"f": float(f), "qber": q, "ci": ci, "theory": 0.25 * f,
                    "abort_rate": float(np.mean([r["aborted"] for r in sub])),
                    "eve_before": mean_ci([r["eve_agreement_before_pa"] for r in sub])[0],
                    "eve_after": mean_ci([r["eve_agreement_after_pa"] for r in sub])[0]})
    SUMMARY["E1"] = agg

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 3.8))
    x = [a["f"] for a in agg]
    ax1.plot(x, [a["theory"] for a in agg], "--", color=INK2, lw=1.2, label="Theory: 0.25 x f")
    ax1.errorbar(x, [a["qber"] for a in agg], yerr=[a["ci"] for a in agg], fmt="o-", color=SERIES[0],
                 capsize=3, label=f"Simulated ({trials} trials, 95% CI)")
    ax1.axhline(pp.QBER_ABORT_THRESHOLD, color=SERIES[1], lw=1.2, ls=":")
    ax1.text(0.01, pp.QBER_ABORT_THRESHOLD + 0.006, "11% abort threshold", color=INK2, fontsize=8)
    ax1.set(xlabel="Fraction of qubits intercepted by Eve", ylabel="QBER", title="QBER vs. eavesdropping")
    ax1.legend(loc="upper left", fontsize=8)
    ax2.bar(x, [a["abort_rate"] for a in agg], width=0.07, color=SERIES[0])
    ax2.set(xlabel="Fraction of qubits intercepted by Eve", ylabel="Share of exchanges aborted",
            title="Eavesdropper detection rate", ylim=(0, 1.05))
    fig.tight_layout()
    fig.savefig(OUT / "e1_qber_vs_eve.png")
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(5.2, 3.6))
    ok = [a for a in agg if not math.isnan(a["eve_after"])]
    ax.plot([a["f"] for a in agg], [a["eve_before"] for a in agg], "o-", color=SERIES[1], label="Before privacy amplification")
    ax.plot([a["f"] for a in ok], [a["eve_after"] for a in ok], "s-", color=SERIES[0], label="After privacy amplification")
    ax.axhline(0.5, color=INK2, lw=1, ls="--")
    ax.text(0.01, 0.505, "random guessing", color=INK2, fontsize=8)
    ax.set(xlabel="Fraction intercepted", ylabel="Eve's bit agreement with key", title="Eve's naive-guess agreement (illustrative)")
    ax.legend(fontsize=8, loc="upper right")
    fig.tight_layout()
    fig.savefig(OUT / "e1b_eve_knowledge.png")
    plt.close(fig)


# --------------------------------------------------------------------------- E2
def e2_noise(trials: int, n: int) -> None:
    print("E2  QBER and key rate vs channel noise")
    rows = []
    ps = np.round(np.linspace(0, 0.3, 13), 3)
    for p in ps:
        noise = ChannelNoise(channel_flip=float(p))
        for t in range(trials):
            r = BB84Protocol(noise=noise, seed=2000 + 41 * t + int(1000 * p)).run(n)
            rows.append({"channel_p": p, "trial": t, "qber_estimate": r.qber_estimate, "qber_true": r.qber_true,
                         "expected_qber": noise.expected_qber(), "aborted": int(r.aborted),
                         "final_length": r.final_length, "key_rate": r.key_rate, "leaked_ec": r.leaked_ec,
                         "errors_corrected": r.errors_corrected})
    save_csv("e2_noise", rows)
    agg = []
    for p in ps:
        sub = [r for r in rows if r["channel_p"] == p]
        q, qci = mean_ci([r["qber_true"] for r in sub])
        k, kci = mean_ci([r["key_rate"] for r in sub])
        agg.append({"p": float(p), "qber": q, "qber_ci": qci, "expected": sub[0]["expected_qber"], "rate": k,
                    "rate_ci": kci, "abort_rate": float(np.mean([r["aborted"] for r in sub]))})
    SUMMARY["E2"] = agg

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 3.8))
    x = [a["p"] for a in agg]
    ax1.plot(x, [a["expected"] for a in agg], "--", color=INK2, lw=1.2, label="Theory: p / 2")
    ax1.errorbar(x, [a["qber"] for a in agg], yerr=[a["qber_ci"] for a in agg], fmt="o-", color=SERIES[0],
                 capsize=3, label="Simulated")
    ax1.axhline(pp.QBER_ABORT_THRESHOLD, color=SERIES[1], lw=1.2, ls=":")
    ax1.text(0.0, pp.QBER_ABORT_THRESHOLD + 0.004, "11% abort threshold", color=INK2, fontsize=8)
    ax1.set(xlabel="Depolarising channel parameter p", ylabel="QBER", title="QBER vs. channel noise")
    ax1.legend(fontsize=8, loc="upper left")

    qq = np.linspace(0, 0.12, 200)
    bound = [0.5 * max(0, 1 - 2 * pp.binary_entropy(q)) for q in qq]
    ax2.plot(qq, bound, "--", color=INK2, lw=1.2, label="Asymptotic bound  ½[1 - 2h(Q)]")
    ax2.errorbar([a["qber"] for a in agg], [a["rate"] for a in agg], yerr=[a["rate_ci"] for a in agg],
                 fmt="o", color=SERIES[0], capsize=3, label=f"Simulated, n = {n} qubits")
    ax2.set(xlabel="QBER", ylabel="Secret bits per qubit sent", title="Secret-key rate vs. QBER", xlim=(-0.005, 0.16))
    ax2.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(OUT / "e2_noise.png")
    plt.close(fig)


# --------------------------------------------------------------------------- E3
def e3_detection(n: int, reps: int) -> None:
    print("E3  Detection probability vs sample size")
    rand = RandomSource(seed=3)
    a, ab, bb = rand.bits(n), rand.bits(n), rand.bits(n)
    rows = []
    for f in (1.0, 0.5, 0.2):
        tx = QuantumChannelSimulator(seed=3).transmit(a, ab, bb, eve=Eavesdropper(f), rand=rand)
        keep = pp.sift(ab, bb)
        err = (a[keep] != tx.bob_results[keep]).astype(int)
        for k in (1, 2, 4, 8, 12, 16, 24, 32, 48, 64):
            hits = sum(err[rand.choice(len(err), k)].any() for _ in range(reps))
            rows.append({"intercept_fraction": f, "sample_bits": k, "empirical": hits / reps,
                         "theory": pp.detection_probability(k, f)})
    save_csv("e3_detection", rows)
    SUMMARY["E3"] = rows

    fig, ax = plt.subplots(figsize=(5.6, 3.8))
    for i, f in enumerate((1.0, 0.5, 0.2)):
        sub = [r for r in rows if r["intercept_fraction"] == f]
        ks = [r["sample_bits"] for r in sub]
        ax.plot(ks, [r["theory"] for r in sub], "--", color=SERIES[i], lw=1.2)
        ax.plot(ks, [r["empirical"] for r in sub], "o", color=SERIES[i], label=f"f = {f:.0%} intercepted")
    ax.set(xlabel="Number of sifted bits compared publicly", ylabel="P(Eve detected)",
           title="Detection probability: 1 - (1 - f/4)^k", ylim=(0, 1.03))
    ax.legend(fontsize=8, loc="lower right", title="markers: simulated\ndashed: theory", title_fontsize=7)
    fig.tight_layout()
    fig.savefig(OUT / "e3_detection.png")
    plt.close(fig)


# --------------------------------------------------------------------------- E4
def e4_scaling(trials: int) -> None:
    print("E4  Scalability and simulator methods")
    from qiskit_aer import AerSimulator

    from qkdchat.quantum import build_bb84_circuit

    rows = []
    for n in (256, 512, 1024, 2048, 4096, 8192, 16384, 32768):
        ts, tt = [], []
        for t in range(trials):
            r = BB84Protocol(seed=4000 + t).run(n)
            ts.append(r.timings["simulate_s"])
            tt.append(r.timings["total_s"])
        rows.append({"n_qubits": n, "simulate_s": float(np.median(ts)), "total_s": float(np.median(tt)),
                     "final_length": r.final_length})
    save_csv("e4_scaling", rows)

    # Method comparison on identical 1024-qubit workloads
    rand = RandomSource(seed=5)
    n = 1024
    a, ab, bb = rand.bits(n), rand.bits(n), rand.bits(n)
    methods = []
    for method, chunk in (("stabilizer", 256), ("matrix_product_state", 256), ("statevector", 16),
                          ("density_matrix", 8)):
        circs = [build_bb84_circuit(a[s:s + chunk], ab[s:s + chunk], bb[s:s + chunk], barriers=False)
                 for s in range(0, n, chunk)]
        sim = AerSimulator(method=method)
        sim.run(circs[:1], shots=1).result()  # warm-up
        t0 = time.perf_counter()
        for _ in range(trials):
            sim.run(circs, shots=1, memory=True, seed_simulator=1).result()
        methods.append({"method": method, "qubits_per_circuit": chunk, "circuits": len(circs),
                        "seconds_per_1024_qubits": (time.perf_counter() - t0) / trials})
    save_csv("e4_methods", methods)
    SUMMARY["E4"] = {"scaling": rows, "methods": methods}

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 3.8))
    ns = [r["n_qubits"] for r in rows]
    ax1.loglog(ns, [r["simulate_s"] for r in rows], "o-", color=SERIES[0], label="Aer simulation")
    ax1.loglog(ns, [r["total_s"] for r in rows], "s-", color=SERIES[1], label="Full protocol (incl. post-processing)")
    ax1.set(xlabel="Qubits per key exchange", ylabel="Seconds (median)", title="Run time vs. key size")
    ax1.legend(fontsize=8)
    labels = [m["method"].replace("matrix_product_state", "MPS").replace("_", " ") + f"\n({m['qubits_per_circuit']} q/circuit)"
              for m in methods]
    ax2.barh(labels, [m["seconds_per_1024_qubits"] for m in methods], color=SERIES[0], height=0.55)
    for i, m in enumerate(methods):
        ax2.text(m["seconds_per_1024_qubits"], i, f"  {m['seconds_per_1024_qubits']:.3f}s", va="center", fontsize=8)
    ax2.set(xlabel="Seconds to simulate 1,024 BB84 qubits", title="Aer simulation method")
    ax2.invert_yaxis()
    ax2.set_xlim(0, max(m["seconds_per_1024_qubits"] for m in methods) * 1.3)
    fig.tight_layout()
    fig.savefig(OUT / "e4_scaling.png")
    plt.close(fig)


# --------------------------------------------------------------------------- E5
def e5_hardware(trials: int, n: int) -> None:
    print("E5  Realistic IBM device noise (fake backends)")
    rows = []
    for name in ("fake_torino", "fake_brisbane", "fake_sherbrooke"):
        for t in range(trials):
            r = BB84Protocol(hardware=name, seed=5000 + t).run(n)
            rows.append({"backend": name, "trial": t, "qber_estimate": r.qber_estimate, "qber_true": r.qber_true,
                         "aborted": int(r.aborted), "final_length": r.final_length, "key_rate": r.key_rate,
                         "simulate_s": r.timings.get("simulate_s", float("nan")),
                         "transpile_s": r.timings.get("transpile_s", float("nan"))})
            print(f"    {name} trial {t}: QBER {r.qber_true:.3f}, key {r.final_length}")
    save_csv("e5_hardware", rows)
    agg = []
    for name in sorted({r["backend"] for r in rows}):
        sub = [r for r in rows if r["backend"] == name]
        agg.append({"backend": name, "qber": mean_ci([r["qber_true"] for r in sub])[0],
                    "key_rate": mean_ci([r["key_rate"] for r in sub])[0],
                    "abort_rate": float(np.mean([r["aborted"] for r in sub])),
                    "simulate_s": mean_ci([r["simulate_s"] for r in sub])[0]})
    SUMMARY["E5"] = agg

    fig, ax = plt.subplots(figsize=(5.6, 3.4))
    names = [a["backend"].replace("fake_", "ibm_") for a in agg]
    ax.bar(names, [a["qber"] for a in agg], color=SERIES[0], width=0.5)
    for i, a in enumerate(agg):
        ax.text(i, a["qber"], f"{a['qber']:.1%}\nrate {a['key_rate']:.3f}", ha="center", va="bottom", fontsize=8)
    ax.axhline(pp.QBER_ABORT_THRESHOLD, color=SERIES[1], lw=1.2, ls=":")
    ax.set(ylabel="QBER", title=f"BB84 under IBM device noise snapshots (n = {n})", ylim=(0, 0.13))
    fig.tight_layout()
    fig.savefig(OUT / "e5_hardware.png")
    plt.close(fig)


# --------------------------------------------------------------------------- E6
def e6_cascade(trials: int) -> None:
    print("E6  Cascade efficiency")
    rows = []
    rng = np.random.default_rng(6)
    n = 4000
    for q in (0.005, 0.01, 0.02, 0.03, 0.05, 0.07, 0.09, 0.11):
        for t in range(trials):
            a = rng.integers(0, 2, n, dtype=np.uint8)
            b = a ^ (rng.random(n) < q).astype(np.uint8)
            real_q = float(np.mean(a != b))
            res = pp.cascade(a, b, real_q, ClassicalChannel(), rng)
            rows.append({"qber": q, "trial": t, "leaked": res.leaked_bits,
                         "efficiency_f": res.leaked_bits / (n * pp.binary_entropy(real_q)) if real_q else float("nan"),
                         "success": int(np.array_equal(res.corrected, a))})
    save_csv("e6_cascade", rows)
    agg = [{"qber": q, "f": mean_ci([r["efficiency_f"] for r in rows if r["qber"] == q])[0],
            "success": float(np.mean([r["success"] for r in rows if r["qber"] == q]))}
           for q in sorted({r["qber"] for r in rows})]
    SUMMARY["E6"] = agg

    fig, ax = plt.subplots(figsize=(5.6, 3.4))
    ax.plot([a["qber"] for a in agg], [a["f"] for a in agg], "o-", color=SERIES[0], label="Cascade (4 passes)")
    ax.axhline(1.0, color=INK2, lw=1, ls="--")
    ax.text(0.0, 1.02, "Shannon limit (f = 1)", color=INK2, fontsize=8)
    ax.set(xlabel="QBER", ylabel="f = leaked bits / n·h(Q)", title="Error-correction efficiency", ylim=(0.9, None))
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(OUT / "e6_cascade.png")
    plt.close(fig)


# --------------------------------------------------------------------------- E7
def e7_classical(trials: int) -> None:
    print("E7  Classical baseline vs BB84")
    from classical_baseline import benchmark_x25519

    cls = benchmark_x25519(trials=max(200, trials * 50))
    q_times, q_qubits = [], []
    for t in range(trials):
        # smallest exchange that reliably yields >= 256 bits on a clean channel
        r = BB84Protocol(seed=7000 + t).run(1024)
        q_times.append(r.timings["total_s"])
        q_qubits.append(r.n_raw * 256 / max(r.final_length, 1))
    row = {"x25519_ms_per_key": 1000 * cls["seconds_per_exchange"],
           "bb84_sim_ms_per_exchange_1024q": 1000 * float(np.median(q_times)),
           "bb84_qubits_per_256bit_key": float(np.median(q_qubits)),
           "x25519_public_bytes": cls["public_bytes"]}
    save_csv("e7_classical_vs_quantum", [row])
    SUMMARY["E7"] = row


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--only", nargs="*")
    args = ap.parse_args()
    style()
    OUT.mkdir(exist_ok=True)
    trials = 3 if args.quick else 10
    run = set(args.only or ["e1", "e2", "e3", "e4", "e5", "e6", "e7"])
    t0 = time.time()
    if "e1" in run:
        e1_eve(trials, 2048)
    if "e2" in run:
        e2_noise(trials, 4096)
    if "e3" in run:
        e3_detection(8192, 2000 if not args.quick else 300)
    if "e4" in run:
        e4_scaling(3 if not args.quick else 1)
    if "e5" in run:
        e5_hardware(3 if not args.quick else 1, 2048)
    if "e6" in run:
        e6_cascade(trials)
    if "e7" in run:
        e7_classical(trials)
    prev = {}
    if (OUT / "summary.json").exists():
        prev = json.loads((OUT / "summary.json").read_text())
    prev.update(SUMMARY)
    prev["_meta"] = {"trials": trials, "runtime_s": round(time.time() - t0, 1)}
    (OUT / "summary.json").write_text(json.dumps(prev, indent=2, default=float))
    print(f"Done in {time.time() - t0:.0f}s -> {OUT}")


if __name__ == "__main__":
    main()
