# Development log (qualitative data, proposal 5.5)

The research methodology planned to document implementation challenges as they occurred. The entry template
below was set up for this, but entries were not written during the build. The eight observations listed here
were compiled after the build from the code, the tests, the results and the development history, and each was
checked against the code. Two details (marked †) appear in the development history but are not preserved in
this repository.

## Observations

1. **API churn (background, not met during this build).** Qiskit 1.0 removed `execute()` and the `qiskit.Aer`
   import, and Qiskit 2.x code differs from much 2022-2023 learning material (`AerSimulator().run`, the
   `SamplerV2` primitive, the `qiskit_ibm_runtime` fake provider). This project was written directly against
   Qiskit 2.6, so this is reported from the documentation, not from experience. (RQ2, RQ5)
2. **Register ordering.** Aer returns classical registers in reverse creation order, separated by spaces, and
   each register is little-endian (qubit 0 is right-most). `_parse_memory()` in `quantum.py` handles this; the
   ordering was checked with known inputs before the parser was written. A wrong order fails silently. (RQ1, RQ2)
3. **Choosing the simulator is a design decision.** Statevector simulation cannot hold 256-qubit circuits, but
   the stabilizer method can, because BB84 is Clifford-only. With device noise (T1/T2 relaxation) the stabilizer
   method is no longer valid and MPS is needed (E4). (RQ2, RQ3)
4. **An opaque failure in Runtime local mode.** `SamplerV2` against a fake backend with 100-qubit circuits raised
   `ValueError: could not broadcast input array from shape (0,13) into shape (1,13)`. Removing the unused `eve`
   register was tried first and did not fix it. The cause was that local mode's default simulator could not hold
   a 100-qubit circuit and returned empty results; the fix was to pass
   `AerSimulator.from_backend(..., method="matrix_product_state")` as the sampler mode. (RQ2, RQ3)
5. **Debugging without observation.** Qubits cannot be printed mid-protocol, so the tests assert statistical
   properties (QBER ≈ 25 % under a full attack, sift ratio 45-55 %, QRNG balance, QBER under noise within
   1.5 points of p/2, Cascade correcting every error). One test's expected gate count was itself wrong at first:
   it predicted two H gates where the circuit has three.† (RQ1, RQ3)
6. **Most of the code is classical.** Excluding comments, blank lines and docstrings, the quantum-specific
   modules (`quantum.py`, `noise.py`, `ibm.py`) are 310 SLOC (27 %), and post-processing, orchestration, crypto,
   key management and the CLI are 841 SLOC (73 %). (RQ3, RQ4)
7. **Finite-size effects (E5b).** Under the IBM Torino noise snapshot, 0/10 exchanges of 300 qubits, 4/10 of
   500, 9/10 of 1 000 and 10/10 of 2 048 produced a key, at a QBER of about 4.8 %. Privacy-amplification
   overhead (the 64-bit tag, the 16-bit margin and Cascade leakage) exhausts short keys. An earlier 300-qubit run
   failed verification after Cascade, which led to the adaptive reconciliation rounds.† (RQ3, RQ4)
8. **Transpilation cost.** Grover with n = 4 grows from depth 38 to 504 (161 two-qubit gates) on Torino's native
   gate set, and its success probability falls from 0.96 to 0.51. (RQ3)

## Entry template (for future work)

```
### YYYY-MM-DD - <short title>
Phase:          design | implementation | testing | evaluation
Component:      quantum layer | post-processing | crypto | UI | tooling
What happened:
Why it was hard / surprising:
Classical equivalent (if any):
Resolution:
Time spent:
Relevance:      RQ1 / RQ2 / RQ3 / RQ4 / RQ5
```
