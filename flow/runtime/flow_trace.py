"""
FLOW — runtime + ExecutionTrace.

Envuelve FlowVM sin alterar su semantica. Produce un ExecutionTrace
determinista (dados mismos programa + seed + config) registrado por
OBSERVACION de la VM real: diffeo de estado particula-a-particula y
reuso del log de instrucciones de la propia VM.

Regla de orden documentada (derivada del runtime actual, NO inventada):
    SPLIT hace particles.append(...) durante el for del tick -> la hija
    puede ejecutar en el MISMO tick N (Python itera la lista mutada).

No re-ejecuta logica. No inventa eventos. Solo justifica desde la VM.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional

import numpy as np

import flow.core as flow_core

ENGINE_VERSION = "flow-0.1+trace-1"

# Mnemonicos B (canal escalar) — copia fiel de flow.py / README.
MNEMONIC = {
    0: "HALT", 1: "NOP", 2: "INC", 3: "DEC", 4: "READ", 5: "WRITE",
    6: "JMP+", 7: "JMP-", 8: "SPLIT", 9: "TURN+", 10: "TURN-",
    11: "PUSH", 12: "POP", 13: "DUP", 14: "SWAP",
    15: "ADD", 16: "SUB", 17: "MUL", 18: "DIV", 19: "MOD", 20: "EQ", 21: "LT",
    22: "RAND", 23: "COLOR", 24: "TRACE", 255: "NOP_SPAWN",
}


def program_hash(image_path: str | Path) -> str:
    """sha256 de los bytes crudos del archivo programa (sin modificarlo)."""
    return hashlib.sha256(Path(image_path).read_bytes()).hexdigest()


@dataclass
class Event:
    tick: int
    pid: int
    type: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"tick": self.tick, "pid": self.pid, "type": self.type,
                "payload": self.payload}


@dataclass
class ExecutionTrace:
    metadata: dict[str, Any]
    ticks: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"metadata": self.metadata, "ticks": self.ticks, "events": self.events}

    def save(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8")


# ---- snapshot helpers ----
def _snap(p: flow_core.Particle) -> dict[str, Any]:
    return {"x": p.x, "y": p.y, "vx": p.vx, "vy": p.vy,
            "state": p.state, "alive": p.alive, "stack_len": len(p.stack),
            "pid": p.pid}


def _snap_all(ps: list[flow_core.Particle]) -> dict[int, dict[str, Any]]:
    return {p.pid: _snap(p) for p in ps}


def run_program(image_path: str | Path,
                config: Optional[flow_core.FlowConfig] = None,
                seed: Optional[int] = None) -> ExecutionTrace:
    """Ejecuta la VM real y devuelve un ExecutionTrace observado."""
    cfg = config or flow_core.FlowConfig()
    if seed is not None:
        cfg.seed = seed

    p_hash = program_hash(image_path)
    vm = flow_core.FlowVM(str(image_path), cfg)

    events: list[dict[str, Any]] = []
    ticks: list[dict[str, Any]] = []

    # SPAWN inicial (spawn marker B==255). Determinista (np.where C-order).
    for p in vm.particles:
        events.append(Event(0, p.pid, "PARTICLE_SPAWN",
                             {"x": p.x, "y": p.y, "src": "marker"}).to_dict())

    while True:
        tick_before = vm.tick
        before = _snap_all(vm.particles)
        field_b_before = vm.field_b.copy()
        trace_before = vm.field_trace.copy()
        alive_before = sum(1 for p in vm.particles if p.alive)

        any_alive = vm.step()
        if not any_alive and vm.tick == tick_before:
            # step no avanzó/particulas muertas -> no hay evento nuevo
            if not any(p.alive for p in vm.particles):
                break

        tick_after = vm.tick  # == tick_before + 1 (step hace tick+=1)
        after = _snap_all(vm.particles)
        # instrucciones ejecutadas este tick: ultimas entradas del log con tick==tick_after-1
        executed: dict[int, int] = {}
        for row in vm.log:
            if row[0] == tick_after - 1:
                executed[row[1]] = row[5]

        # INSTRUCTION_EXECUTED + efectos por particula
        for pid, instr in executed.items():
            events.append(Event(tick_after - 1, pid, "INSTRUCTION_EXECUTED",
                                {"instr": instr, "mnemonic": MNEMONIC.get(int(instr), "NOP")}).to_dict())
        for pid in executed:
            s_after = after.get(pid)
            s_before = before.get(pid)
            if s_before is None or s_after is None:
                continue  # particula creada este tick sin snapshot previo
            # STATE_CHANGE
            if s_after["state"] != s_before["state"]:
                events.append(Event(tick_after - 1, pid, "STATE_CHANGE",
                                    {"from": s_before["state"], "to": s_after["state"]}).to_dict())
            # PARTICLE_TURN (vel cambio y la instruccion fue TURN+/-)
            if (s_after["vx"] != s_before["vx"] or s_after["vy"] != s_before["vy"]) and executed[pid] in (9, 10):
                events.append(Event(tick_after - 1, pid, "PARTICLE_TURN",
                                    {"vx_before": s_before["vx"], "vy_before": s_before["vy"],
                                     "vx_after": s_after["vx"], "vy_after": s_after["vy"],
                                     "dir": "+" if executed[pid] == 9 else "-"}).to_dict())
            # PARTICLE_DEATH
            if s_before["alive"] and not s_after["alive"]:
                events.append(Event(tick_after - 1, pid, "PARTICLE_DEATH",
                                    {"x": s_after["x"], "y": s_after["y"]}).to_dict())
            # PARTICLE_MOVE (posicion cambio por MOVE, detectada post-step)
            if s_after["alive"] and (s_after["x"] != s_before["x"] or s_after["y"] != s_before["y"]):
                events.append(Event(tick_after - 1, pid, "PARTICLE_MOVE",
                                    {"x_from": s_before["x"], "y_from": s_before["y"],
                                     "x_to": s_after["x"], "y_to": s_after["y"]}).to_dict())

        # PARTICLE_SPAWN (pids nuevos este tick -> SPLIT)
        for pid in sorted(set(after) - set(before)):
            s = after[pid]
            events.append(Event(tick_after - 1, pid, "PARTICLE_SPAWN",
                                 {"x": s["x"], "y": s["y"], "src": "SPLIT",
                                  "parent_hint": s_before and "present"}).to_dict())

        # FIELD_WRITE / TRACE (diff del campo)
        bdiff = np.argwhere((vm.field_b != field_b_before))
        if bdiff.size:
            # limitar a algunos puntos representativos (los WRITE/COLOR son puntuales)
            for (y, x) in bdiff[:32]:
                events.append(Event(tick_after - 1, executed.get(next((pid for pid, s in after.items()
                                                                       if int(round(s["x"])) == x and int(round(s["y"])) == y), -1), -1),
                                    "FIELD_WRITE",
                                    {"x": int(x), "y": int(y),
                                     "value": int(vm.field_b[y, x])}).to_dict())
        tdiff = np.argwhere((vm.field_trace != trace_before))
        if tdiff.size:
            for (y, x) in tdiff[:32]:
                events.append(Event(tick_after - 1, -1, "PARTICLE_TRACE",
                                    {"x": int(x), "y": int(y),
                                     "value": int(vm.field_trace[y, x])}).to_dict())

        ticks.append({"tick": tick_after - 1,
                      "alive_before": alive_before,
                      "alive_after": sum(1 for p in vm.particles if p.alive),
                      "total_particles": len(vm.particles)})

        if not any_alive:
            break
        if vm.tick >= cfg.max_ticks:
            ticks[-1]["reason"] = "max_ticks_reached"
            break

    meta = {
        "engine": "FLOW",
        "engine_version": ENGINE_VERSION,
        "program_sha256": p_hash,
        "seed": cfg.seed,
        "config": {"max_ticks": cfg.max_ticks, "dt": cfg.dt,
                   "damping": cfg.damping, "field_gain": cfg.field_gain,
                   "trace_enabled": cfg.trace_enabled,
                   "image_size": [vm.w, vm.h]},
        "ticks": len(ticks),
        "particles_spawned": vm.pid_counter,
        "final_alive": sum(1 for p in vm.particles if p.alive),
        "execution_order_rule": ("SPLIT appends to particles during the tick loop; "
                                 "a child MAY execute in the same tick N (current VM semantics)"),
    }
    return ExecutionTrace(metadata=meta, ticks=ticks, events=events)


# ---- comparacion semantica de traces ----
def canonical_projection(trace: ExecutionTrace | dict | str) -> dict:
    """Proyeccion determinista: metadata semantica + eventos. Excluye timestamp/session."""
    if isinstance(trace, ExecutionTrace):
        d = trace.to_dict()
    elif isinstance(trace, str):
        with open(trace, encoding="utf-8") as f:
            d = json.load(f)
    else:
        d = dict(trace)
    meta = d.get("metadata", {})
    keep_meta = {k: meta.get(k) for k in
                 ("engine_version", "program_sha256", "ticks",
                  "particles_spawned", "final_alive")}
    return {"metadata": keep_meta, "events": d.get("events", []),
            "ticks": d.get("ticks", [])}


def trace_diff(a: ExecutionTrace | dict, b: ExecutionTrace | dict) -> dict:
    pa, pb = canonical_projection(a), canonical_projection(b)
    if pa == pb:
        return {"verdict": "identical"}
    # detallar primera divergencia
    ev_a, ev_b = pa["events"], pb["events"]
    common = min(len(ev_a), len(ev_b))
    first_diff = None
    for i in range(common):
        if ev_a[i] != ev_b[i]:
            first_diff = {"index": i, "a": ev_a[i], "b": ev_b[i]}
            break
    return {
        "verdict": "different",
        "identical": False,
        "events_count_a": len(ev_a), "events_count_b": len(ev_b),
        "ticks_a": pa["metadata"].get("ticks"),
        "ticks_b": pb["metadata"].get("ticks"),
        "first_diff": first_diff,
    }
