# QKD Chat - a hybrid quantum-classical BB84 application built with Qiskit


Alice and Bob establish secret keys with the **BB84 quantum key distribution protocol**
over a quantum channel simulated with **Qiskit Aer**, then chat over a classical channel
using those keys (one-time pad or AES-256-GCM). An eavesdropper, Eve, can be switched on at
any time. Her intercept-resend attack pushes the quantum bit error rate (QBER) to about 25 %,
so Alice and Bob detect her and throw the key away.

```
python -m qkdchat --demo          # 30-second scripted demonstration
python -m qkdchat                 # interactive chat (type /help)
```

## What the application does (proposal section 9)

| Requirement | Where |
|---|---|
| Simulate the BB84 QKD protocol | `qkdchat/quantum.py` (circuits, Aer), `qkdchat/protocol.py` (orchestration) |
| Generate shared cryptographic keys | sifting → QBER estimation → Cascade → verification → Toeplitz privacy amplification (`qkdchat/postprocessing.py`) |
| Demonstrate secure message exchange | `qkdchat/link.py`, `qkdchat/crypto.py` (OTP and AES-256-GCM) |
| Command-line or Jupyter interface | `qkdchat/cli.py` (rich CLI), `notebooks/` (3 executed notebooks) |
| Integrate quantum and classical components | quantum layer ↔ public channel (`qkdchat/channel.py`, authentication assumed) ↔ key pools ↔ crypto ↔ chat |

Beyond the brief, it also includes:

* **Eavesdropper detection**: intercept-resend at any fraction 0-1, with the ground truth of what Eve learned.
* **Noise models**: a parameterised depolarising + readout channel, or **IBM device noise snapshots**
  (`fake_torino`, `fake_brisbane`, `fake_sherbrooke`, `fake_kyiv`).
* **Path to IBM Quantum hardware**: `experiments/run_on_ibm.py` swaps the quantum layer for Qiskit Runtime
  `SamplerV2`. Nothing else in the pipeline changes. This path has been tested only in Runtime local mode
  against fake backends; it has **not** been run on a live IBM quantum processor, and no reported result
  comes from hardware.
* **Quantum RNG option** (`--qrng`): Alice's and Bob's random choices come from H-and-measure circuits.
  On a simulator this is still pseudo-random underneath; all reported experiments used the seeded pseudo-random source.
* **Experiments** E1-E7 and E5b (finite-size) with CSVs and figures in `results/`, plus a study of four selected quantum algorithms (objective 3).
* **27 automated tests** (`pytest`).

## Installation

Requires Python 3.10+ (developed and tested on Python 3.13, Qiskit 2.6, Qiskit Aer 0.18 and qiskit-ibm-runtime 0.51;
`requirements.txt` pins these versions so that the results can be reproduced).

```bash
git clone https://github.com/<your-username>/qkd-chat.git && cd qkd-chat
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pytest -q                                              # 27 passed
```

## Using the chat

```
Alice> Hello Bob                       # sent as the current speaker
Alice> bob: Hi Alice                   # send as a specific party
Alice> /eve 1.0                        # Eve intercepts every qubit
Alice> /rekey                          # run BB84 now -> QBER ~25 %, ABORTED
Alice> /eve 0  then  /noise 0.06       # honest but noisy channel -> QBER ~3 %, key still produced
Alice> /trace 12                       # qubit-by-qubit table: bits, bases, states, errors
Alice> /circuit 4 eve                  # draw a BB84 circuit with Eve
Alice> /public                         # everything Eve could read on the classical channel
Alice> /mode aes                       # AES-256-GCM with a QKD-derived key
Alice> /hardware fake_torino           # IBM Torino noise snapshot
```

Command-line options: `--mode otp|aes --qubits N --eve F --noise P --readout P --hardware NAME --qrng --seed S --demo`.

## Reproducing the results

```bash
python experiments/run_experiments.py          # E1-E7 and E5b -> results/*.csv, *.png, summary.json
python algorithms/quantum_algorithms.py        # Bell, Deutsch-Jozsa, Bernstein-Vazirani, Grover
python experiments/classical_baseline.py       # X25519 + AES-GCM classical counterpart
python experiments/run_on_ibm.py --backend fake_torino --qubits 500   # IBM Runtime path, local dry run
```

All experiments use fixed seeds, so rerunning the scripts reproduces the files in `results/`. Each exchange in
E1, E2 and E5b is labelled with its outcome: `key`, `threshold` (sampled QBER above 11 %, i.e. Eve detected) or
`no_secret_bits` (QBER below the threshold, but privacy amplification left no secret bits).

To run on a real QPU, save an IBM Quantum API key once
(`QiskitRuntimeService.save_account(channel="ibm_quantum_platform", token="...")`), then run
`python experiments/run_on_ibm.py --qubits 1000`. Each run is logged to `results/ibm_runs.csv`.

## Repository layout

```
qkdchat/                 the application package
  quantum.py             quantum simulation layer: encoding, BB84 circuits, Eve, Aer execution, QRNG
  noise.py               parameterised noise + IBM fake-backend noise snapshots
  channel.py             public channel with a disclosure log (what Eve sees); authentication is assumed
  postprocessing.py      sifting, QBER, Cascade, verification, Toeplitz privacy amplification
  protocol.py            BB84Protocol -> KeyExchangeReport (one full key exchange)
  keypool.py             per-party key storage, bits never reused
  crypto.py              one-time pad and AES-256-GCM (HKDF from the QKD key)
  link.py                SecureLink - key management + encrypted messaging (UI-agnostic)
  cli.py                 rich command-line chat interface
  ibm.py                 Qiskit Runtime drop-in for the quantum layer (tested on fake backends only)
experiments/             E1-E7 evaluation, classical baseline, IBM hardware runner
algorithms/              selected quantum algorithms (objective 3)
notebooks/               01 walkthrough, 02 results, 03 algorithms (all executed)
tests/                   pytest suite
results/                 generated data and figures
docs/                    ARCHITECTURE.md, EVALUATION.md, DEVELOPMENT_LOG.md
```

## Documentation

* [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md): how the code maps onto the conceptual design (proposal Figure 6), the design decisions behind it, and its limitations.
* [`docs/EVALUATION.md`](docs/EVALUATION.md): a first summary of the measured results, organised by research question and evaluation criterion.
* [`docs/DEVELOPMENT_LOG.md`](docs/DEVELOPMENT_LOG.md): the log template and the development observations, which were compiled after the build.



## Licence

Released under the MIT Licence; see [`LICENSE`](LICENSE).
