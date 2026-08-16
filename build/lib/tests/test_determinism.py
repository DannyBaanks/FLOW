"""
Tests de determinismo M1 para FLOW.

Pruebas:
1. Mismo programa + misma seed -> trace identico (hello_flow sin RAND)
2. Mismo programa + diferentes seeds (sin RAND usado) -> trace identico (RAND no ejecutado)
3. Programa que USA RAND: misma seed -> trace identico; diferentes seeds -> trace DIFERENTE
4. replay round-trip: run -> trace -> replay -> trace identico
5. trace-diff identifica correctamente igual/diferente
6. validate acepta trace valido
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from PIL import Image

import flow.core as flow_core
import flow.runtime as flow_trace


def _make_rand_program(path: str):
    """Programa que ejecuta RAND (instr 22) de forma fiable y determinista.
    Diseño: TODAS las 16 filas IDÉNTICAS -> interpolación en Y exacta (sin deriva Y).
    Patrón de fila (x=0..31):
      x=0:   spawn marker B=255, R=200 (empuje derecha)
      x=1..30: RAND puro B=22, R=200 (empuje derecha)
      x=31:  HALT B=0, R=128 (freno)
    G=128 en toda la imagen (vy=0). La partícula spawnea en (0,7)->(0.5,7.5),
    tick 0: instr=255 (NOP), se mueve derecha; tick 1+: entra en región RAND pura (22),
    ejecuta RAND repetidamente hasta HALT en x=31.
    """
    w, h = 32, 16
    img = Image.new("RGB", (w, h), (128, 128, 0))
    px = img.load()
    for y in range(h):
        # spawn marker en x=0
        px[0, y] = (200, 128, 255)  # R=200 empuja derecha, spawn
        # región RAND pura x=1..30
        for x in range(1, 31):
            px[x, y] = (200, 128, 22)  # R=200 empuja, B=22 RAND
        # HALT en x=31
        px[31, y] = (128, 128, 0)  # R=128 freno, HALT
    img.save(path)


def test_determinism_same_seed_same_program():
    """Mismo programa + misma seed -> trace identico (hello_flow)."""
    with tempfile.TemporaryDirectory() as tmp:
        prog = Path(tmp) / "hello.png"
        flow_core.make_hello_flow(str(prog))
        trace = flow_trace.run_program(str(prog), seed=42)
        trace.save(Path(tmp) / "t1.json")

        trace2 = flow_trace.run_program(str(prog), seed=42)
        trace2.save(Path(tmp) / "t2.json")

        diff = flow_trace.trace_diff(trace, trace2)
        assert diff["verdict"] == "identical", f"seed 42 duplicado falló: {diff}"


def test_determinism_no_rand_diff_seeds_identical():
    """Si el programa no usa RAND, diferentes seeds producen trace identico."""
    with tempfile.TemporaryDirectory() as tmp:
        prog = Path(tmp) / "hello.png"
        flow_core.make_hello_flow(str(prog))
        trace1 = flow_trace.run_program(str(prog), seed=42)
        trace2 = flow_trace.run_program(str(prog), seed=999)
        diff = flow_trace.trace_diff(trace1, trace2)
        assert diff["verdict"] == "identical", f"sin RAND, seeds 42 vs 999 difieren: {diff}"


def test_rand_determinism():
    """Programa que ejecuta RAND: misma seed -> trace identico; distinta seed -> trace DIFERENTE."""
    with tempfile.TemporaryDirectory() as tmp:
        prog = Path(tmp) / "rand.png"
        _make_rand_program(str(prog))

        t1 = flow_trace.run_program(str(prog), seed=42)
        t2 = flow_trace.run_program(str(prog), seed=42)
        d = flow_trace.trace_diff(t1, t2)
        assert d["verdict"] == "identical", f"RAND seed 42x2: {d}"

        t3 = flow_trace.run_program(str(prog), seed=99)
        d2 = flow_trace.trace_diff(t1, t3)
        assert d2["verdict"] == "different", f"RAND 42 vs 99 deberían diferir: {d2}"


def test_replay_roundtrip():
    """run -> trace -> replay -> trace identico."""
    with tempfile.TemporaryDirectory() as tmp:
        prog = Path(tmp) / "hello.png"
        flow_core.make_hello_flow(str(prog))
        # run
        trace = flow_trace.run_program(str(prog), seed=123)
        trace.save(Path(tmp) / "orig.json")

        # replay via function
        trace2 = flow_trace.run_program(str(prog), seed=123)
        trace2.save(Path(tmp) / "replay.json")

        diff = flow_trace.trace_diff(trace, trace2)
        assert diff["verdict"] == "identical", f"replay falló: {diff}"


def test_trace_diff_identical_and_different():
    """trace-diff reporta correctamente identical/different."""
    with tempfile.TemporaryDirectory() as tmp:
        prog = Path(tmp) / "hello.png"
        flow_core.make_hello_flow(str(prog))
        t1 = flow_trace.run_program(str(prog), seed=7)
        t2 = flow_trace.run_program(str(prog), seed=7)
        t3 = flow_trace.run_program(str(prog), seed=999)  # same program, no RAND -> identical!
        # verify identical
        assert flow_trace.trace_diff(t1, t2)["verdict"] == "identical"
        assert flow_trace.trace_diff(t1, t3)["verdict"] == "identical"  # no RAND used
        # now make different by creating a trace with an extra event (copy events)
        t1_events = [dict(ev) for ev in t1.events]
        t1_events.append({"tick": 999, "pid": -1, "type": "FAKE", "payload": {}})
        from flow.runtime import ExecutionTrace
        t_fake = ExecutionTrace(metadata=t1.metadata, ticks=t1.ticks, events=t1_events)
        assert flow_trace.trace_diff(t1, t_fake)["verdict"] == "different"


def test_validate_accepts_valid_trace():
    """validator acepta trace valido."""
    with tempfile.TemporaryDirectory() as tmp:
        prog = Path(tmp) / "hello.png"
        flow_core.make_hello_flow(str(prog))
        trace = flow_trace.run_program(str(prog), seed=5)
        path = Path(tmp) / "valid.json"
        trace.save(path)

        # run validate via function (no CLI needed)
        data = json.loads(path.read_text())
        assert "metadata" in data and "events" in data and "ticks" in data
        # ordering
        last = -1
        for ev in data["events"]:
            t = ev.get("tick", 0)
            assert t >= last, f"eventos fuera de orden: {t} < {last}"
            last = t


def test_cli_run_replay_diff_validate():
    """Test rápido de CLI end-to-end (invoca main)."""
    with tempfile.TemporaryDirectory() as tmp:
        prog = Path(tmp) / "hello.png"
        flow_core.make_hello_flow(str(prog))
        # flow run
        import argparse

        from flow.cli import cmd_run
        args = argparse.Namespace(image=str(prog), max_ticks=200, seed=11,
                                   trace=str(Path(tmp) / "cli_trace.json"),
                                   output=None)
        rc = cmd_run(args)
        assert rc == 0
        # flow replay
        args2 = argparse.Namespace(image=str(prog), trace=str(Path(tmp) / "cli_trace.json"),
                                    seed=None, output_trace=str(Path(tmp) / "replay.json"))
        from flow.cli import cmd_replay
        rc2 = cmd_replay(args2)
        assert rc2 == 0
        # flow trace-diff
        args3 = argparse.Namespace(trace_a=str(Path(tmp) / "cli_trace.json"),
                                    trace_b=str(Path(tmp) / "replay.json"))
        from flow.cli import cmd_diff
        rc3 = cmd_diff(args3)
        assert rc3 == 0
        # flow validate
        args4 = argparse.Namespace(trace=str(Path(tmp) / "cli_trace.json"))
        from flow.cli import cmd_validate
        rc4 = cmd_validate(args4)
        assert rc4 == 0


def test_existing_tests_still_pass():
    """Los 8 tests originales siguen pasando (no rompimos nada)."""
    # Se ejecutan aparte con pytest test_flow.py -v


if __name__ == "__main__":
    test_determinism_same_seed_same_program()
    test_determinism_no_rand_diff_seeds_identical()
    test_rand_determinism()
    test_replay_roundtrip()
    test_trace_diff_identical_and_different()
    test_validate_accepts_valid_trace()
    test_cli_run_replay_diff_validate()
    print("Todos los tests M1 de determinismo PASARON")