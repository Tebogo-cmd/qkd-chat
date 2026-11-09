"""Generates the three teaching / evaluation notebooks (run once, then execute with nbconvert)."""

from pathlib import Path

import nbformat as nbf

HERE = Path(__file__).parent
SETUP = """import sys, pathlib
ROOT = pathlib.Path.cwd().resolve().parent if pathlib.Path.cwd().name == 'notebooks' else pathlib.Path.cwd()
sys.path.insert(0, str(ROOT))
import numpy as np, pandas as pd
import matplotlib.pyplot as plt
%matplotlib inline"""


def nb(cells):
    book = nbf.v4.new_notebook()
    book.metadata["kernelspec"] = {"name": "python3", "display_name": "Python 3", "language": "python"}
    book.cells = [nbf.v4.new_markdown_cell(c[1:]) if c.startswith("#") or c.startswith("\n#") else nbf.v4.new_code_cell(c)
                  for c in cells]
    return book


def md(s):
    return "#" + s if not s.startswith("#") else "#" + s


walkthrough = [
    md("""# BB84 Quantum Key Distribution with Qiskit - step-by-step walkthrough

*Honours research artefact - "An Exploratory Analysis of Quantum Computing Programming Using Qiskit" (T. Diraditsile, CPUT, 2026).*

This notebook walks through every layer of the hybrid quantum-classical QKD application (proposal Figure 6):

1. Qubit encoding in two bases (superposition)
2. Measurement collapse and why the *wrong* basis gives a random bit
3. The full BB84 circuit, executed on the Qiskit Aer simulator
4. Eve's intercept-resend attack and the 25 % QBER signature
5. Classical post-processing - sifting, QBER estimation, Cascade, privacy amplification
6. Using the key: encrypted chat between Alice and Bob"""),
    SETUP,
    md("""## 1. Encoding a bit in two bases

| Bit | Z basis (+) | X basis (×) |
|---|---|---|
| 0 | \\|0⟩ | \\|+⟩ = (\\|0⟩+\\|1⟩)/√2 |
| 1 | \\|1⟩ | \\|−⟩ = (\\|0⟩−\\|1⟩)/√2 |

In Qiskit the X-basis states are made by applying a Hadamard gate after the optional X gate. Below we look at the statevectors."""),
    """from qiskit import QuantumCircuit
from qiskit.quantum_info import Statevector
from qkdchat.quantum import encode_qubit, Z_BASIS, X_BASIS, STATE_SYMBOL

for bit in (0, 1):
    for basis in (Z_BASIS, X_BASIS):
        qc = QuantumCircuit(1)
        encode_qubit(qc, 0, bit, basis)
        sv = Statevector(qc)
        print(f"bit={bit} basis={'Z' if basis == Z_BASIS else 'X'}  ->  {STATE_SYMBOL[(bit, basis)]:4}  amplitudes = {np.round(sv.data, 3)}")""",
    md("""## 2. Measurement in the right vs. wrong basis

Measuring |+⟩ in the Z basis collapses it to 0 or 1 with 50 % probability each - Bob learns nothing. Measuring it in the X basis (H then measure) returns 0 every time. This asymmetry is the whole basis of BB84 security."""),
    """from qiskit_aer import AerSimulator
from qkdchat.quantum import measure_qubit
sim = AerSimulator()
for meas_basis in (Z_BASIS, X_BASIS):
    qc = QuantumCircuit(1, 1)
    encode_qubit(qc, 0, 0, X_BASIS)       # Alice sends |+>
    measure_qubit(qc, 0, 0, meas_basis)   # Bob measures
    counts = sim.run(qc, shots=2000, seed_simulator=1).result().get_counts()
    print(f"|+> measured in {'Z' if meas_basis == Z_BASIS else 'X'} basis: {dict(sorted(counts.items()))}")""",
    md("""## 3. The BB84 circuit

`build_bb84_circuit` puts every transmitted qubit on its own wire. The `I` gate is the quantum channel - noise models attach errors to it. Six qubits are shown here; the application packs 256 qubits per circuit and uses Aer's stabilizer method, because every gate in BB84 is a Clifford gate."""),
    """from qkdchat.quantum import demo_circuit, BASIS_SYMBOL
qc, info = demo_circuit(6, with_eve=False, seed=3)
print("Alice bits :", info['alice_bits'])
print("Alice bases:", [BASIS_SYMBOL[int(b)] for b in info['alice_bases']])
print("Bob bases  :", [BASIS_SYMBOL[int(b)] for b in info['bob_bases']])
qc.draw('mpl', fold=-1, style='clifford')""",
    md("## 4. One key exchange, qubit by qubit"),
    """from qkdchat import BB84Protocol, Eavesdropper, ChannelNoise
proto = BB84Protocol(seed=42)
rep = proto.run(2048)
tx = proto.last_transmission

def trace(tx, k=16):
    return pd.DataFrame({
        'Alice bit': tx.alice_bits[:k], 'Alice basis': [BASIS_SYMBOL[int(b)] for b in tx.alice_bases[:k]],
        'State': [STATE_SYMBOL[(int(a), int(b))] for a, b in zip(tx.alice_bits[:k], tx.alice_bases[:k])],
        'Eve basis': [BASIS_SYMBOL[int(b)] if m else '-' for b, m in zip(tx.eve_bases[:k], tx.eve_mask[:k])],
        'Bob basis': [BASIS_SYMBOL[int(b)] for b in tx.bob_bases[:k]], 'Bob result': tx.bob_results[:k],
        'Sifted': tx.alice_bases[:k] == tx.bob_bases[:k],
        'Error': (tx.alice_bases[:k] == tx.bob_bases[:k]) & (tx.alice_bits[:k] != tx.bob_results[:k]),
    })
trace(tx)""",
    """pd.Series(rep.to_dict()).drop(['timings', 'public_channel']).to_frame('clean channel')""",
    md("""## 5. Eve attacks: intercept-resend

Eve measures each qubit in a random basis and re-sends what she saw. Half the time she picks the wrong basis and disturbs the state; Bob then gets a wrong bit half of those times → **QBER = ½ × ½ = 25 %**. Rows marked `Error` below are her fingerprints."""),
    """proto_eve = BB84Protocol(eve=Eavesdropper(1.0), seed=42)
rep_eve = proto_eve.run(2048)
display(trace(proto_eve.last_transmission))
print(f"Estimated QBER = {rep_eve.qber_estimate:.1%}  ->  aborted: {rep_eve.aborted} ({rep_eve.abort_reason})")""",
    md("""## 6. Post-processing on the classical channel

* **Sifting** keeps only positions where the bases matched (≈50 %).
* **Parameter estimation** reveals a random 25 % of the sifted bits and discards them.
* **Cascade** fixes remaining errors by comparing block parities; each disclosed parity is one bit leaked to Eve.
* **Privacy amplification** compresses the key with a random Toeplitz matrix to `n[1 − h(Q)] − leak` bits.

Below, a noisy (but honest) channel: errors exist, Cascade removes them, and both parties end up with *identical* keys."""),
    """rep_noisy = BB84Protocol(noise=ChannelNoise(channel_flip=0.08), seed=7).run(4096)
cols = ['n_raw','n_sifted','n_sample','n_reconciled','qber_estimate','qber_true','errors_corrected','leaked_ec','residual_errors','verified','final_length','key_rate','keys_match']
pd.DataFrame([{k: rep.to_dict()[k] for k in cols}, {k: rep_noisy.to_dict()[k] for k in cols}], index=['clean', 'noisy p=0.08']).T""",
    """print("What Eve sees on the public channel:")
pd.DataFrame(BB84Protocol(noise=ChannelNoise(channel_flip=0.08), seed=7).run(4096).public_channel).T""",
    md("## 7. Using the key: encrypted chat"),
    """from qkdchat import SecureLink
link = SecureLink(mode='otp', seed=1)
for sender, text in [('Alice', 'Hi Bob! This is protected by BB84.'), ('Bob', 'Got it - one-time pad, unbreakable.')]:
    d = link.send(sender, text)
    print(f"{d.sender} -> {d.receiver}\\n  wire : {d.ciphertext.hex(64)}\\n  reads: {d.decrypted}\\n  key bits used: {d.key_bits_used}\\n")
link.stats()""",
    """link.set_eve(1.0)
try:
    link.send('Alice', 'x' * 400)   # needs a fresh key -> BB84 runs under attack
except Exception as e:
    print(type(e).__name__, '-', e)""",
    md("""## 8. Realistic hardware noise (optional, ~10 s)

`hardware='fake_torino'` replaces the idealised noise with a calibration snapshot of IBM's 133-qubit *Torino* processor (T1/T2, gate and readout errors), so the protocol runs as it would on that device - without needing an IBM account."""),
    """rep_hw = BB84Protocol(hardware='fake_torino', seed=3).run(2048)
print(f"backend={rep_hw.backend}  QBER={rep_hw.qber_true:.2%}  final key={rep_hw.final_length} bits  rate={rep_hw.key_rate:.3f}")""",
]

experiments = [
    md("""# Experimental results

Figures and tables produced by `experiments/run_experiments.py` and `algorithms/quantum_algorithms.py`. Re-run those scripts to regenerate the data (`results/*.csv`). This notebook only loads and displays them."""),
    SETUP + "\nfrom IPython.display import Image, display\nR = ROOT / 'results'",
    md("## E1 - QBER and detection vs. Eve's intercept fraction"),
    "display(Image(R / 'e1_qber_vs_eve.png'))\npd.read_csv(R / 'e1_qber_vs_eve.csv').groupby('intercept_fraction')[['qber_true', 'aborted', 'final_length']].mean().round(4)",
    "display(Image(R / 'e1b_eve_knowledge.png', width=520))",
    md("## E2 - Channel noise and secret-key rate"),
    "display(Image(R / 'e2_noise.png'))\npd.read_csv(R / 'e2_noise.csv').groupby('channel_p')[['expected_qber', 'qber_true', 'key_rate', 'aborted']].mean().round(4)",
    md("## E3 - How many sampled bits are needed to catch Eve?"),
    "display(Image(R / 'e3_detection.png', width=560))\npd.read_csv(R / 'e3_detection.csv').pivot(index='sample_bits', columns='intercept_fraction', values='empirical')",
    md("## E4 - Run time and simulator methods"),
    "display(Image(R / 'e4_scaling.png'))\ndisplay(pd.read_csv(R / 'e4_scaling.csv'))\npd.read_csv(R / 'e4_methods.csv')",
    md("## E5 - IBM device noise snapshots"),
    "display(Image(R / 'e5_hardware.png', width=560))\npd.read_csv(R / 'e5_hardware.csv').groupby('backend')[['qber_true', 'key_rate', 'final_length', 'simulate_s']].mean().round(4)",
    md("## E6 - Cascade reconciliation efficiency"),
    "display(Image(R / 'e6_cascade.png', width=560))\npd.read_csv(R / 'e6_cascade.csv').groupby('qber')[['efficiency_f', 'success']].mean().round(3)",
    md("## E7 - Classical baseline (X25519) vs. simulated BB84"),
    "pd.read_csv(R / 'e7_classical_vs_quantum.csv').T",
    md("## Selected quantum algorithms (objective 3)"),
    "display(Image(R / 'algorithms.png'))\npd.read_csv(R / 'algorithms.csv')",
]

algorithms = [
    md("""# Selected quantum algorithms in Qiskit

Bell state, Deutsch-Jozsa, Bernstein-Vazirani and Grover's search - each drawn, run on the ideal simulator and on a noise model of IBM Torino. The point for the research questions: *the same algorithm that is perfect in simulation degrades as transpiled depth grows on NISQ hardware.*"""),
    SETUP + "\nsys.path.insert(0, str(ROOT / 'algorithms'))\nfrom quantum_algorithms import *\nfrom qiskit.visualization import plot_histogram\nfrom qiskit_aer import AerSimulator",
    md("## Bell state - superposition + entanglement"),
    "qc = bell_state(); display(qc.draw('mpl'))\nplot_histogram(AerSimulator().run(qc, shots=2000, seed_simulator=1).result().get_counts())",
    md("## Deutsch-Jozsa - constant or balanced in one query"),
    "qc = deutsch_jozsa(3, True, '101'); display(qc.draw('mpl'))\nplot_histogram(AerSimulator().run(qc, shots=2000, seed_simulator=1).result().get_counts())",
    md("## Bernstein-Vazirani - recover a hidden string in one query"),
    "qc = bernstein_vazirani('1011'); display(qc.draw('mpl'))\nplot_histogram(AerSimulator().run(qc, shots=2000, seed_simulator=1).result().get_counts())",
    md("## Grover's search - amplitude amplification"),
    "qc = grover('101'); display(qc.draw('mpl', fold=-1))\nplot_histogram(AerSimulator().run(transpile(qc, AerSimulator()), shots=2000, seed_simulator=1).result().get_counts())",
    md("## Ideal vs. noisy hardware, all algorithms"),
    "rows = run_all('fake_torino')\npd.DataFrame(rows).drop(columns=['top_ideal', 'top_noisy'])",
]

for name, cells in [("01_BB84_walkthrough", walkthrough), ("02_experiment_results", experiments), ("03_quantum_algorithms", algorithms)]:
    nbf.write(nb(cells), HERE / f"{name}.ipynb")
    print("wrote", name)
