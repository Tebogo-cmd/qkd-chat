"""Run one BB84 key exchange on IBM Quantum hardware (proposal 5.3: "IBM Quantum
Platform may be used to access real quantum devices for experimental validation").

    # dry-run locally with a device noise snapshot (no account needed)
    python experiments/run_on_ibm.py --backend fake_torino --qubits 500

    # real QPU (least busy device on your account), optionally with Eve
    python experiments/run_on_ibm.py --qubits 1000
    python experiments/run_on_ibm.py --backend ibm_torino --qubits 1000 --eve 1.0

Results are appended to results/ibm_runs.csv.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qkdchat import BB84Config, BB84Protocol, Eavesdropper  # noqa: E402
from qkdchat.ibm import IBMRuntimeChannel  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default=None, help="e.g. ibm_torino, or fake_torino for a local dry run")
    ap.add_argument("--qubits", type=int, default=1000)
    ap.add_argument("--eve", type=float, default=0.0)
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()

    proto = BB84Protocol(BB84Config(), eve=Eavesdropper(args.eve) if args.eve else None, seed=args.seed)
    proto.simulator = IBMRuntimeChannel(args.backend, seed=args.seed)  # swap the quantum layer
    print(f"Submitting {args.qubits} qubits to {proto.simulator.backend_name} ...")
    rep = proto.run(args.qubits)

    print(f"job id        : {proto.simulator.last_job_id}")
    print(f"QBER (sample) : {rep.qber_estimate:.2%}   (true {rep.qber_true:.2%})")
    print(f"result        : {'ABORTED - ' + rep.abort_reason if rep.aborted else f'{rep.final_length}-bit key, keys match: {rep.keys_match}'}")
    print(f"QPU wall time : {rep.timings.get('simulate_s', 0):.1f}s")

    out = ROOT / "results" / "ibm_runs.csv"
    out.parent.mkdir(exist_ok=True)
    row = {"timestamp": dt.datetime.now().isoformat(timespec="seconds"), "backend": rep.backend,
           "job_id": proto.simulator.last_job_id, "eve": args.eve, **{k: v for k, v in rep.to_dict().items()
                                                                       if k not in ("timings", "public_channel")}}
    new = not out.exists()
    with open(out, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(row.keys()))
        if new:
            w.writeheader()
        w.writerow(row)


if __name__ == "__main__":
    main()
