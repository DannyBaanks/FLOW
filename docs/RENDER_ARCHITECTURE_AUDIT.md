# RENDER ARCHITECTURE AUDIT — FLOW (pre multi-projection change)

Date: 2026-09-03. Repo: `FLOW` @ commit f05a0de + local README tweak.
Python 3.12.4, Pillow 12.3.0, NumPy 2.4.6. Baseline test run: **24/24 PASS**.

## CURRENT_EXECUTION_PIPELINE

```
program.png (R/G = vector field, B = scalar/instructions+spawn markers)
  -> FlowVM (flow/core/flow.py)
       step(): per alive particle: READ instr (bilinear B sample), EXECUTE
       (exec_instruction), FIELD update (vx,vy), MOVE (x,y), BOUNDS kill.
       tick += 1 at end of each step. SPLIT appends a child to the particle
       list DURING the tick loop (child may execute same tick). RAND uses
       random.Random(cfg.seed) -> deterministic given seed.
  -> run_program (flow/runtime/flow_trace.py)
       Observes the VM: diffs particle snapshots tick-by-tick + reads vm.log
       + diffs field_b/field_trace. Emits Event dicts. Never invents events.
  -> ExecutionTrace {metadata, ticks[], events[]}
       metadata: engine, engine_version ("flow-0.1+trace-1"), program_sha256,
       seed, config{max_ticks,dt,damping,field_gain,trace_enabled,image_size},
       ticks, particles_spawned, final_alive, execution_order_rule.
       ticks[]: {tick, alive_before, alive_after, total_particles}.
       events[]: {tick, pid, type, payload} — types:
         PARTICLE_SPAWN (src marker|SPLIT), INSTRUCTION_EXECUTED
         (instr + mnemonic), STATE_CHANGE, PARTICLE_TURN, PARTICLE_DEATH,
         PARTICLE_MOVE (x_from,y_from,x_to,y_to), FIELD_WRITE,
         PARTICLE_TRACE (x,y,value).
       Ordering: non-decreasing tick (validated by `flow validate`).
  -> trace.json (json.dumps indent=2, utf-8)
```

Determinism today: same program + seed + config -> identical canonical
projection (`canonical_projection` excludes seed/session-ish fields;
`trace_diff` compares it). Verified by `flow replay` + tests.

## CURRENT_RENDER_PIPELINE

`flow.render` (M2) consumes ONLY an ExecutionTrace (dict | path | object);
never re-runs the VM (`cmd_render` is trace-only by design).

- `draw.py` — `TraceScene.from_trace`: derives spawns/deaths/moves/cells/
  instruction-counts from events. Position lookup = last known position at
  or before tick. `new_arena`, `draw_cells`, `draw_trails`, `draw_particles`,
  `draw_deaths`, deterministic palettes (`pid_color` golden-angle,
  `value_color`). Background/grid constants.
- `image.py` — `render_image` (PNG; layouts `arena`|`split` w/ text panel).
- `gif.py` — `render_gif` (one frame per trace tick, evenly subsampled to
  `--max-frames`).
- `session.py` — `render_session_gif` ("fake terminal" panel + arena; all
  numbers from trace metadata; typed script is fixed/deterministic).
- CLI: `flow render TRACE --format {image,gif,session} --layout --scale
  --duration --max-frames --program --output`.

The renderer reads NOTHING besides the trace (plus fixed styling constants
and the optional `--program` display string in session mode).

## SEMANTIC_BOUNDARY

Everything in `flow/core/` + `flow/runtime/`: program bytes, FlowConfig, VM
step rules, ExecutionTrace contents (metadata/ticks/events) and its JSON
serialization. These define **what happened**. Unchanged by this work.

## PRESENTATION_BOUNDARY

Everything in `flow/render/` + the `render` CLI: colors, scale, layout, frame
sub-sampling, panels, typed text, GIF encoding. These define **how it is
shown**. The trace is read-only input.

Crossing rules (added by this work):
- `TraceScene` already derives a visual state per tick from events; this is
  presentation. New profiles add INTERPOLATED frames between ticks and
  PERSISTENT visual effects. An interpolated position is a
  PRESENTATION frame, NOT a VM state (documented in FASE 7 contract).
- EVENT_OCCURRENCE_COUNT (trace) != EVENT_VISUAL_DURATION (render):
  an event row occurs exactly once in the trace; a pulse may remain visible
  for N frames.

## RISKS

- R1 Confusing interpolated/persistent visuals with execution truth ->
  mitigated by explicit contracts + `VERDICTS.md` + tests that count events
  from the trace, not from frames.
- R2 Pillow GIF encoder nondeterminism -> mitigated by byte-compare tests
  (already passes for existing renderers); classify BYTE_ vs FRAME_
  DETERMINISTIC with evidence.
- R3 Story/spec modes inventing semantics -> mitigated by schema validation:
  unknown profile/spec key/event mapping = FAIL; entities may only bind to
  real pids/event types of the given trace.
- R4 Breaking existing CLI/tests -> existing `--format` path kept verbatim;
  profiles are additive (`--profile`), default behavior unchanged.
```
