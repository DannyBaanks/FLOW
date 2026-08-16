#!/usr/bin/env python3
"""
CLI FLOW — comandos deterministas sobre el runtime real.
Subcomandos:
  run       -> ejecuta y guarda trace.json (+ outputs legacy)
  replay    -> re-ejecuta mismo programa+seed+config y verifica igualdad
  trace-diff -> compara dos trace.json (proyeccion semantica)
  validate  -> valida schema de trace
  (render sera M2)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import flow.core as flow_core
from flow.runtime import flow_trace

ENGINE_VERSION = "flow-0.1+cli-1"


def _load_trace(path: Path) -> flow_trace.ExecutionTrace:
    data = json.loads(path.read_text(encoding="utf-8"))
    return flow_trace.ExecutionTrace(metadata=data.get("metadata", {}),
                                      ticks=data.get("ticks", []),
                                      events=data.get("events", []))


def _save_outputs(vm: flow_core.FlowVM, out_prefix: str):
    vm.save_output(out_prefix)
    vm.save_log(f"{out_prefix}.csv")


def cmd_run(args: argparse.Namespace) -> int:
    from flow import FlowConfig, FlowVM  # lazy import

    config = FlowConfig(max_ticks=args.max_ticks)
    seed = args.seed
    vm = FlowVM(args.image, FlowConfig(max_ticks=args.max_ticks, seed=seed))
    trace = flow_trace.run_program(args.image, seed=seed)
    trace.save(args.trace)
    print(f"Trace -> {args.trace}")

    # outputs legacy (field B + trace + csv) opcional
    if args.output:
        # need to re-run with outputs or capture from VM; easier: run VM directly
        vm2 = FlowVM(args.image, FlowConfig(max_ticks=args.max_ticks, seed=seed))
        vm2.run()
        vm2.save_output(args.output)
        vm2.save_log(f"{args.output}.csv")
        print(f"Salida legacy -> {args.output}_b.png, {args.output}.csv")

    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    """Re-ejecuta el mismo programa con misma seed+config y verifica trace."""
    p_hash = flow_trace.program_hash(args.image)
    # extraer metadata del trace original para comparar config exacta
    with open(args.trace, encoding="utf-8") as f:
        orig = json.load(f)
    seed = orig.get("metadata", {}).get("seed")
    if seed is None and args.seed is not None:
        seed = args.seed
    cfg = flow_core.FlowConfig(max_ticks=orig.get("metadata", {}).get("config", {}).get("max_ticks", 10000),
                               dt=orig.get("metadata", {}).get("config", {}).get("dt", 1.0),
                               damping=orig.get("metadata", {}).get("config", {}).get("damping", 0.9),
                               field_gain=orig.get("metadata", {}).get("config", {}).get("field_gain", 0.1),
                               trace_enabled=orig.get("metadata", {}).get("config", {}).get("trace_enabled", True),
                               seed=seed)

    trace2 = flow_trace.run_program(args.image, config=flow_core.FlowConfig(
        max_ticks=cfg.max_ticks, dt=cfg.dt, damping=cfg.damping,
        field_gain=cfg.field_gain, trace_enabled=cfg.trace_enabled, seed=seed))
    trace2.save(args.output_trace if args.output_trace else args.trace + ".replay.json")

    # cargar trace original para diff
    with open(args.trace, encoding="utf-8") as f:
        trace_orig_data = json.load(f)
    diff = flow_trace.trace_diff(trace_orig_data, trace2)
    if diff["verdict"] == "identical":
        print("REPLAY OK — trace idéntico al original")
        return 0
    else:
        print(f"REPLAY DIFERENTE — {json.dumps(diff, indent=2)}")
        return 1


def cmd_diff(args: argparse.Namespace) -> int:
    diff = flow_trace.trace_diff(args.trace_a, args.trace_b)
    print(json.dumps(diff, indent=2, ensure_ascii=False))
    return 0 if diff.get("verdict") == "identical" else 1


def cmd_validate(args: argparse.Namespace) -> int:
    with open(args.trace, encoding="utf-8") as f:
        data = json.load(f)
    # Schema basico
    required = ("metadata", "ticks", "events")
    for r in required:
        if r not in data:
            print(f"FAIL: falta {r} en trace")
            return 1
    # eventos ordenados por tick non-decreasing
    events = data.get("events", [])
    last_tick = -1
    for ev in events:
        t = ev.get("tick", 0)
        if t < last_tick:
            print(f"FAIL: eventos fuera de orden en tick {t} < {last_tick}")
            return 1
        last_tick = t
    # metadata campos criticos
    meta = data.get("metadata", {})
    for k in ("engine_version", "program_sha256", "seed"):
        if k not in meta:
            print(f"WARN: metadata falta {k}")
    # conteos
    print(f"OK: trace valido — {len(events)} eventos, {len(data.get('ticks', []))} ticks")
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    """Render image/GIF/session from a trace (M2). Trace-only: never re-runs the VM."""
    from flow.render import render_gif, render_image, render_session_gif

    if args.format == "session":
        out = Path(args.output).with_suffix(".gif")
        render_session_gif(
            args.trace,
            out,
            program_name=args.program,
            layout=args.layout,
            scale=args.scale,
            max_frames=args.max_frames,
            duration_ms=args.duration,
        )
        print(f"Session GIF -> {out}")
    elif args.format == "gif":
        out = Path(args.output).with_suffix(".gif")
        render_gif(
            args.trace,
            out,
            layout=args.layout,
            scale=args.scale,
            max_frames=args.max_frames,
            duration_ms=args.duration,
        )
        print(f"GIF -> {out}")
    else:
        out = Path(args.output).with_suffix(".png")
        render_image(args.trace, out, layout=args.layout, scale=args.scale)
        print(f"PNG -> {out}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="flow", description="FLOW deterministic runner")
    sub = parser.add_subparsers(dest="cmd", required=True)

    # run
    p_run = sub.add_parser("run", help="Ejecuta programa y guarda trace.json")
    p_run.add_argument("image", help="PNG programa")
    p_run.add_argument("--max-ticks", type=int, default=10000)
    p_run.add_argument("--seed", type=int, default=None, help="Seed determinista para RAND")
    p_run.add_argument("--trace", default="trace.json", help="Archivo trace de salida")
    p_run.add_argument("--output", "-o", help="Prefijo legacy outputs (_b.png, .csv)")
    p_run.set_defaults(func=cmd_run)

    # replay
    p_replay = sub.add_parser("replay", help="Re-ejecuta y verifica igualdad")
    p_replay.add_argument("image", help="PNG programa")
    p_replay.add_argument("--trace", default="trace.json", help="Trace original")
    p_replay.add_argument("--seed", type=int, help="Override seed (si trace no la tiene)")
    p_replay.add_argument("--output-trace", help="Donde guardar trace del replay")
    p_replay.set_defaults(func=cmd_replay)

    # trace-diff
    p_diff = sub.add_parser("trace-diff", help="Compara dos trace.json semanticamente")
    p_diff.add_argument("trace_a")
    p_diff.add_argument("trace_b")
    p_diff.set_defaults(func=cmd_diff)

    # validate
    p_val = sub.add_parser("validate", help="Valida schema de trace.json")
    p_val.add_argument("trace")
    p_val.set_defaults(func=cmd_validate)

    # render (M2)
    p_render = sub.add_parser("render", help="Renderiza image/gif/session desde un trace (M2)")
    p_render.add_argument("trace", help="trace.json existente (nunca re-ejecuta la VM)")
    p_render.add_argument("--format", choices=["image", "gif", "session"], default="image")
    p_render.add_argument("--layout", choices=["arena", "split"], default="arena")
    p_render.add_argument("--scale", type=int, default=6)
    p_render.add_argument("--duration", type=int, default=85, help="ms por frame (gif/session)")
    p_render.add_argument("--max-frames", type=int, default=220, help="frames max (gif/session)")
    p_render.add_argument("--program", default="vortex.png", help="nombre del programa (solo session)")
    p_render.add_argument("--output", "-o", default="flow_render", help="Ruta salida")
    p_render.set_defaults(func=cmd_render)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())