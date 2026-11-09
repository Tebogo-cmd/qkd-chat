"""Command-line chat interface for the QKD application (top layer of Figure 6).

Run:
    python -m qkdchat                 # interactive chat
    python -m qkdchat --demo          # scripted demonstration (good for presentations)
    python -m qkdchat --eve 1.0       # start with a full intercept-resend attacker
    python -m qkdchat --hardware fake_torino   # IBM device noise snapshot

Type a message to send it as the current speaker, or prefix it with
``alice:`` / ``bob:``. Type ``/help`` for commands.
"""

from __future__ import annotations

import argparse
import shlex
import sys

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import __version__
from .link import EavesdropperDetected, SecureLink
from .noise import HARDWARE_BACKENDS, ChannelNoise
from .protocol import BB84Config, KeyExchangeReport
from .quantum import BASIS_SYMBOL, STATE_SYMBOL, demo_circuit

console = Console()
COLOURS = {"Alice": "green", "Bob": "cyan", "Eve": "red"}

HELP = """[bold]Messaging[/bold]
  <text>                 send as the current speaker
  alice: <text>          send as Alice      bob: <text>   send as Bob
  /as alice|bob          switch current speaker

[bold]Quantum key distribution[/bold]
  /rekey [n]             run BB84 now with n qubits (default from --qubits)
  /eve <0..1>            intercept-resend fraction (0 = off, 1 = every qubit)
  /noise <p> [readout]   depolarising channel noise p (and optional readout error)
  /hardware <name|off>   use an IBM device noise snapshot (""" + ", ".join(HARDWARE_BACKENDS) + """)
  /mode otp|aes          one-time pad or AES-256-GCM with a QKD key

[bold]Inspection[/bold]
  /trace [k]             qubit-by-qubit table of the last exchange (first k qubits)
  /circuit [n] [eve]     draw a small BB84 circuit
  /public                what was disclosed on the public channel (Eve sees all of it)
  /stats                 link statistics
  /help, /quit"""


# --------------------------------------------------------------------------- #
# Rendering helpers
# --------------------------------------------------------------------------- #
def pct(x: float) -> str:
    return "n/a" if x != x else f"{100 * x:.1f}%"


def report_table(rep: KeyExchangeReport, title: str = "BB84 key exchange") -> Table:
    t = Table(title=title, box=box.SIMPLE_HEAVY, show_header=False, title_style="bold magenta")
    t.add_column("Stage", style="bold")
    t.add_column("Value")
    t.add_row("Backend", rep.backend)
    t.add_row("Qubits sent", str(rep.n_raw))
    t.add_row("Sifted (bases matched)", f"{rep.n_sifted}  ({pct(rep.sift_ratio)})")
    t.add_row("Sampled & discarded", str(rep.n_sample))
    qcol = "red" if rep.qber_estimate > 0.11 else ("yellow" if rep.qber_estimate > 0 else "green")
    t.add_row("QBER (estimated)", Text(pct(rep.qber_estimate), style=qcol))
    t.add_row("QBER (true, simulation only)", pct(rep.qber_true))
    if rep.eve_intercepted:
        t.add_row("Eve intercepted", Text(f"{rep.eve_intercepted} qubits", style="red"))
    if rep.aborted:
        t.add_row("Result", Text("ABORTED - " + rep.abort_reason, style="bold red"))
        return t
    t.add_row("Cascade: errors fixed / bits leaked", f"{rep.errors_corrected} / {rep.leaked_ec}")
    t.add_row("Verification", Text("hash tags match" if rep.verified else "FAILED", style="green" if rep.verified else "red"))
    t.add_row("Final secret key", Text(f"{rep.final_length} bits  (rate {rep.key_rate:.3f} bits/qubit)", style="bold green"))
    t.add_row("Eve's agreement before -> after PA", f"{pct(rep.eve_agreement_before_pa)} -> {pct(rep.eve_agreement_after_pa)}")
    t.add_row("Time", f"{1000 * rep.timings.get('total_s', 0):.0f} ms  (simulate {1000 * rep.timings.get('simulate_s', 0):.0f} ms)")
    return t


def show_delivery(d) -> None:
    for rep in d.rekeyed:
        console.print(report_table(rep, "New key generated (BB84)"))
    s, r = d.sender, d.receiver
    body = Text()
    body.append(f"{s:<5} types : ", style="bold")
    body.append(d.plaintext + "\n")
    body.append("On the wire : ", style="bold red")
    body.append(f"[{d.ciphertext.mode}] {d.ciphertext.hex(64)}\n", style="red")
    body.append("Eve   reads : ", style="bold red")
    body.append(d.eve_view[:64] + "\n", style="dim red")
    body.append(f"{r:<5} reads : ", style="bold")
    body.append(d.decrypted, style=COLOURS[r])
    status = "[green]decrypted OK[/green]" if d.ok else "[red]MISMATCH[/red]"
    used = f"{d.key_bits_used} key bits used" if d.key_bits_used else "session key reused"
    console.print(
        Panel(body, title=f"[{COLOURS[s]}]{s}[/] -> [{COLOURS[r]}]{r}[/]", subtitle=f"{status} - {used}", expand=False)
    )


def show_trace(link: SecureLink, k: int = 16) -> None:
    tx = link.protocol.last_transmission
    if tx is None:
        console.print("[yellow]No exchange yet - use /rekey[/yellow]")
        return
    t = Table(title=f"First {k} qubits of the last exchange", box=box.MINIMAL_DOUBLE_HEAD)
    for col in ("#", "Alice bit", "Alice basis", "State sent", "Eve basis", "Bob basis", "Bob result", "Sifted?", "Match?"):
        t.add_column(col, justify="center")
    for i in range(min(k, tx.n_qubits)):
        a, ab, bb, br = int(tx.alice_bits[i]), int(tx.alice_bases[i]), int(tx.bob_bases[i]), int(tx.bob_results[i])
        eb = BASIS_SYMBOL[int(tx.eve_bases[i])] if tx.eve_mask[i] else "-"
        same = ab == bb
        match = ("[green]yes[/green]" if a == br else "[red]ERROR[/red]") if same else ""
        t.add_row(str(i), str(a), BASIS_SYMBOL[ab], STATE_SYMBOL[(a, ab)], eb, BASIS_SYMBOL[bb], str(br),
                  "yes" if same else "[dim]no[/dim]", match)
    console.print(t)


def show_public(link: SecureLink) -> None:
    t = Table(title="Public (classical) channel - everything Eve can read", box=box.SIMPLE)
    t.add_column("Message type")
    t.add_column("Count", justify="right")
    t.add_column("Bits", justify="right")
    for kind, d in link.protocol.channel.summary().items():
        t.add_row(kind, str(d["messages"]), str(d["bits"]))
    console.print(t)


def show_stats(link: SecureLink) -> None:
    t = Table(title="Link statistics", box=box.SIMPLE, show_header=False)
    for k, v in link.stats().items():
        t.add_row(k.replace("_", " "), f"{v:.4f}" if isinstance(v, float) else str(v))
    t.add_row("mode", link.mode)
    t.add_row("eve", str(link.eve.intercept_fraction) if link.eve else "off")
    t.add_row("noise", str(link.noise))
    t.add_row("hardware", link.hardware or "off")
    console.print(t)


# --------------------------------------------------------------------------- #
# Command handling
# --------------------------------------------------------------------------- #
class ChatApp:
    def __init__(self, link: SecureLink, n_qubits: int):
        self.link = link
        self.n_qubits = n_qubits
        self.speaker = "Alice"

    def rekey(self, n: int | None = None) -> None:
        try:
            with console.status("Running BB84 on the Qiskit Aer simulator..."):
                rep = self.link.establish_key(n or self.n_qubits)
            console.print(report_table(rep))
        except EavesdropperDetected as e:
            console.print(report_table(e.report, "BB84 key exchange"))
            console.print("[bold red]Key discarded. Alice and Bob refuse to use a compromised key.[/bold red]")

    def send(self, sender: str, text: str) -> None:
        try:
            with console.status("Encrypting (running BB84 if more key is needed)..."):
                d = self.link.send(sender, text)
            show_delivery(d)
        except EavesdropperDetected as e:
            console.print(report_table(e.report))
            console.print(
                f"[bold red]Eavesdropping detected on {self.link.max_attempts} consecutive attempts - "
                "message NOT sent.[/bold red] Use /eve 0 to remove the attacker."
            )

    def handle(self, line: str) -> bool:
        line = line.strip()
        if not line:
            return True
        low = line.lower()
        for name in ("alice", "bob"):
            if low.startswith(name + ":"):
                self.send(name.capitalize(), line[len(name) + 1 :].strip())
                return True
        if not line.startswith("/"):
            self.send(self.speaker, line)
            return True

        parts = shlex.split(line[1:])
        cmd, args = parts[0].lower(), parts[1:]
        try:
            if cmd in ("quit", "exit", "q"):
                return False
            elif cmd == "help":
                console.print(Panel(HELP, title="Commands", expand=False))
            elif cmd == "as":
                self.speaker = args[0].capitalize()
                assert self.speaker in ("Alice", "Bob")
                console.print(f"Now speaking as [{COLOURS[self.speaker]}]{self.speaker}[/]")
            elif cmd == "rekey":
                self.rekey(int(args[0]) if args else None)
            elif cmd == "eve":
                f = float(args[0]) if args else 1.0
                self.link.set_eve(f)
                console.print(f"[red]Eve intercepts {pct(f)} of qubits[/red]" if f else "[green]Eve removed[/green]")
                if self.link.mode == "aes":
                    console.print("[dim]AES mode reuses the session key - run /rekey to see the effect.[/dim]")
            elif cmd == "noise":
                p = float(args[0]) if args else 0.0
                r = float(args[1]) if len(args) > 1 else 0.0
                self.link.set_noise(ChannelNoise(channel_flip=p, readout_error=r))
                console.print(f"Channel noise p={p}, readout={r} (expected QBER ~{pct(self.link.noise.expected_qber())})")
            elif cmd == "hardware":
                name = args[0] if args else "fake_torino"
                self.link.hardware = None if name == "off" else name
                with console.status(f"Loading {name} noise model..."):
                    self.link._build_protocol()
                console.print(f"Hardware noise: {self.link.hardware or 'off'}")
            elif cmd == "mode":
                self.link.set_mode(args[0])
                console.print(f"Encryption mode: {args[0]}")
            elif cmd == "trace":
                show_trace(self.link, int(args[0]) if args else 16)
            elif cmd == "circuit":
                n = int(args[0]) if args else 4
                qc, info = demo_circuit(n, with_eve=bool(args[1:]) and args[1] == "eve")
                console.print(f"Alice bits {info['alice_bits']}  bases {[BASIS_SYMBOL[int(b)] for b in info['alice_bases']]}")
                console.print(f"Bob bases  {[BASIS_SYMBOL[int(b)] for b in info['bob_bases']]}")
                console.print(qc.draw(output="text", fold=120).single_string(), highlight=False, markup=False)
            elif cmd == "public":
                show_public(self.link)
            elif cmd == "stats":
                show_stats(self.link)
            else:
                console.print(f"[yellow]Unknown command /{cmd} - try /help[/yellow]")
        except (IndexError, ValueError, AssertionError) as exc:
            console.print(f"[yellow]Bad arguments for /{cmd}: {exc}. Try /help[/yellow]")
        return True

    def loop(self) -> None:
        console.print(
            Panel(
                "[bold]QKD Chat[/bold] - BB84 quantum key distribution with Qiskit\n"
                "Alice and Bob share keys over a simulated quantum channel, then chat over a classical one.\n"
                "Type a message, or [bold]/help[/bold] for commands.",
                title=f"qkdchat v{__version__}",
                expand=False,
            )
        )
        while True:
            try:
                line = console.input(f"[{COLOURS[self.speaker]}]{self.speaker}>[/] ")
            except (EOFError, KeyboardInterrupt):
                console.print()
                break
            if not self.handle(line):
                break
        show_stats(self.link)


DEMO_SCRIPT = [
    ("say", "== 1. Clean channel: establish a key and chat (one-time pad) =="),
    ("cmd", "/rekey 2048"),
    ("cmd", "/trace 12"),
    ("cmd", "alice: Hi Bob, this message is protected by a quantum key."),
    ("cmd", "bob: Received! Nobody else can read it."),
    ("say", "== 2. Eve performs a full intercept-resend attack =="),
    ("cmd", "/eve 1.0"),
    ("cmd", "/rekey 2048"),
    ("say", "== 3. Eve leaves; a noisy (but honest) channel =="),
    ("cmd", "/eve 0"),
    ("cmd", "/noise 0.06"),
    ("cmd", "/rekey 4096"),
    ("say", "== 4. AES-256-GCM mode: one QKD key, many messages =="),
    ("cmd", "/mode aes"),
    ("cmd", "alice: Switching to AES-256 with a QKD-derived key."),
    ("cmd", "bob: Much less key material needed per message."),
    ("cmd", "/public"),
]


def run_demo(app: ChatApp) -> None:
    for kind, text in DEMO_SCRIPT:
        if kind == "say":
            console.rule(f"[bold yellow]{text}")
        else:
            console.print(f"[dim]> {text}[/dim]")
            app.handle(text)
    show_stats(app.link)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="qkdchat", description="BB84 QKD chat application built with Qiskit")
    ap.add_argument("--mode", choices=["otp", "aes"], default="otp", help="encryption mode (default otp)")
    ap.add_argument("--qubits", type=int, default=2048, help="qubits per BB84 exchange")
    ap.add_argument("--eve", type=float, default=0.0, help="intercept-resend fraction 0..1")
    ap.add_argument("--noise", type=float, default=0.0, help="depolarising channel noise")
    ap.add_argument("--readout", type=float, default=0.0, help="detector readout error")
    ap.add_argument("--hardware", choices=HARDWARE_BACKENDS, help="IBM device noise snapshot")
    ap.add_argument("--qrng", action="store_true", help="use a Qiskit quantum RNG for Alice/Bob")
    ap.add_argument("--seed", type=int, default=None, help="seed for reproducible runs")
    ap.add_argument("--demo", action="store_true", help="run the scripted demonstration and exit")
    args = ap.parse_args(argv)

    link = SecureLink(
        mode=args.mode,
        config=BB84Config(n_qubits=args.qubits),
        noise=ChannelNoise(channel_flip=args.noise, readout_error=args.readout),
        hardware=args.hardware,
        seed=args.seed,
        rng_kind="quantum" if args.qrng else "pseudo",
    )
    if args.eve:
        link.set_eve(args.eve)
    app = ChatApp(link, args.qubits)
    if args.demo:
        run_demo(app)
    else:
        app.loop()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
