# Evaluation results

> **Status: first summary.** This file counts every exchange that did not produce a key as "aborted". The later
> analysis (thesis and paper) separates *threshold aborts* (sampled QBER above 11 %, i.e. Eve detected) from
> exchanges where privacy amplification left *no secret bits*. At f = 0.3, all three "aborts" below were
> no-secret-bits outcomes, not detections. The E5b finite-size data are in `results/e5b_finite_size.csv`.

All numbers below come from `python experiments/run_experiments.py` (10 trials per point, seeded)
and `python algorithms/quantum_algorithms.py`. Raw data is in `results/*.csv` and figures in `results/*.png`.
Rerunning the scripts regenerates every number here. The **interpretation and discussion in the
technical report are yours**: this file records what was measured and where it connects to the
proposal.

## Evaluation criteria (proposal 5.6)

| Criterion | Evidence | Result |
|---|---|---|
| Correct implementation of BB84 | E1, E2, E3; 27 tests | Measured QBER follows theory: **0.25·f** under intercept-resend (25.3 % at f = 1) and **p/2** under depolarising noise. Sift ratio is about 50 %. Keys are bit-identical after reconciliation in every non-aborted run. |
| Ease of development using Qiskit | DEVELOPMENT_LOG.md, code size | The quantum layer is about 240 lines. Most of the effort went into the classical post-processing and application (~950 lines). See RQ3 below. |
| Educational value | notebooks/, CLI `/trace`, `/circuit`, `/public` | Each concept (superposition, collapse, the basis mismatch, eavesdropping disturbance) has a visible, runnable artefact. |
| Practical limitations | E4, E5, algorithms | Simulation cost, device noise, finite-size effects and transpiled depth (see below). |
| Suitability for software engineering | ARCHITECTURE.md | A layered design with a swappable quantum backend (simulator, noise snapshot or QPU) behind one interface. |

## E1 - Eavesdropping detection (`e1_qber_vs_eve.png`)

| Eve intercept fraction f | 0 | 0.1 | 0.2 | 0.3 | 0.4 | 0.5 | 1.0 |
|---|---|---|---|---|---|---|---|
| Measured QBER | 0.0 % | 2.6 % | 5.1 % | 7.4 % | 10.5 % | 12.7 % | 25.3 % |
| Theory (0.25·f) | 0 % | 2.5 % | 5 % | 7.5 % | 10 % | 12.5 % | 25 % |
| Exchanges aborted | 0 % | 0 % | 0 % | 30 % | 100 % | 100 % | 100 % |

Observation: with f ≤ 0.3, Eve stays under the 11 % threshold. The protocol still produces a key,
and privacy amplification shrinks it to remove what she could have learned (`e1b_eve_knowledge.png`).
So "undetected" does not mean "successful".

## E2 - Channel noise and key rate (`e2_noise.png`)

QBER tracks the analytic p/2 across p = 0-0.3. The secret-key rate falls from **0.35 bits/qubit**
(clean) to 0.12 at QBER ≈ 4.8 % and reaches zero at about 9-10 % QBER. That is below the asymptotic
11 % limit because of the finite key length (4,096 qubits), Cascade's inefficiency and the
verification and security margins.

## E3 - How quickly is Eve caught? (`e3_detection.png`)

Simulated detection probabilities match **P = 1 − (1 − f/4)^k**. A full attack is caught with 68 %
probability from 4 compared bits, 99 % from 16 and effectively 100 % from 64. A 20 % attack needs
about 64 bits for 96 % detection.

## E4 - Run time and simulator choice (`e4_scaling.png`)

* Simulation time grows linearly with key size: 0.06 s for 1,024 qubits and 1.8 s for 32,768.
  The full protocol at 32,768 qubits takes 6.8 s, dominated by the O(n·m) Toeplitz hash.
* For 1,024 BB84 qubits: **stabilizer 0.07 s, MPS 0.05 s, statevector 0.37 s (16 qubits/circuit),
  density matrix 0.72 s (8 qubits/circuit)**. Choosing a simulator that matches the circuit structure
  is a quantum-specific engineering decision with no direct classical analogue.

## E5 - IBM device noise snapshots (`e5_hardware.png`, 2,048 qubits, 3 trials)

| Device snapshot | Mean QBER | Key rate (bits/qubit) |
|---|---|---|
| ibm_brisbane (Eagle) | 3.1 % | 0.185 |
| ibm_sherbrooke (Eagle) | 3.7 % | 0.124 |
| ibm_torino (Heron) | 4.6 % | 0.119 |

None aborted at 2,048 qubits, but 300-qubit runs on the Torino snapshot all failed. The sample was
too small to estimate the QBER reliably, or privacy amplification left no bits. This is a concrete
**finite-size effect**.

## E6 - Cascade efficiency (`e6_cascade.png`)

Leakage is **1.11-1.22× the Shannon limit** n·h(Q) for QBER 0.5-11 %, with a 100 % correction success
rate. This is in line with values reported in the Cascade literature.

## E7 - Classical baseline (`e7_classical_vs_quantum.csv`)

| | X25519 Diffie-Hellman | BB84 (simulated) |
|---|---|---|
| Time for one 256-bit key | **0.2 ms** | ~87 ms for one 1,024-qubit exchange |
| Resources | 64 public bytes | about 915 qubits per 256 secret bits, plus ~1,000+ bits of public discussion |
| Security basis | hardness of the elliptic-curve discrete log (broken by Shor) | no-cloning and measurement disturbance (information-theoretic) |
| Code | ~10 lines, deterministic | quantum layer + 5-stage post-processing, probabilistic |

## Selected algorithms (objective 3, `algorithms.png`)

| Algorithm | Logical depth → depth on Torino | 2-qubit gates | P(correct), ideal | P(correct), Torino noise |
|---|---|---|---|---|
| Bell state | 3 → 7 | 1 | 1.00 | 0.98 |
| Deutsch-Jozsa n=3 (balanced) | 6 → 14 | 2 | 1.00 | 1.00 |
| Bernstein-Vazirani (1011) | 7 → 15 | 3 | 1.00 | 0.96 |
| Grover n=2 | 12 → 10 | 2 | 1.00 | 0.97 |
| Grover n=3 | 22 → 134 | 37 | 0.94 | 0.80 |
| Grover n=4 | 38 → 504 | 161 | 0.96 | 0.51 |

The multi-controlled gates in Grover's algorithm expand into many native two-qubit gates during
transpilation, and accuracy falls with depth. This is the NISQ limitation from the literature
review, now measured directly.

## Mapping to the research questions

| RQ | Main evidence in this artefact |
|---|---|
| 1. Paradigm differences | Probabilistic output (shots, QBER), no observation without disturbance (`/trace`), and reversible gates and circuits vs. sequential code. Compare E7. |
| 2. Using Qiskit to implement and simulate | `quantum.py`, `algorithms/`, simulator methods (E4), noise models (E2, E5), Runtime path (`ibm.py`) |
| 3. Advantages and limitations vs. classical | E4, E5, E7, the algorithms table and DEVELOPMENT_LOG.md |
| 4. Implementing QKD with Qiskit | The whole application, plus E1-E3 and E6 |
| 5. Teachability | Notebook 01, the CLI inspection commands and the development log. Consider a short peer walkthrough if your supervisor allows it (it would need ethics review, since proposal 5.7 says there are no human participants). |
