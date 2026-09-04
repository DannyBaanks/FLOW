# VERDICTS — Multi-Render Projections

Date: 2026-09-03. Fixture: `examples/vortex.png`, seed=42, max_ticks=500.
Trace: `9E00FA881B234C9F5559B9F73DC42745DAEBCCBBE7E6AE75D5EE71D8F34FF666`.

## Semantic invariants

| Verdict | Result | Evidence |
|---------|--------|----------|
| FLOW_VM_SEMANTICS_CHANGED | **NO** | `flow/core/flow.py` untouched; no execution logic modified |
| EXECUTION_TRACE_SCHEMA_CHANGED | **NO** | `flow/runtime/flow_trace.py` untouched; trace format identical |
| TRACE_CHANGED_BY_RENDERER | **NO** | SHA-256 of `execution.trace.json` unchanged before/after all 7 profile renders (verified in `test_trace_never_mutated_by_render`) |
| INTERPOLATION_PRESENTATION_ONLY | **DEMONSTRATED** | `test_interpolated_frames_are_presentation_only`: event count in trace is identical regardless of interpolation factor (1,3,6,10) |
| EVENT_PERSISTENCE_PRESENTATION_ONLY | **DEMONSTRATED** | `test_persistence_does_not_alter_event_count`: trace event count unchanged across persistence_frames=1,5,15 |
| DECORATIVE_EFFECTS_NEVER_ENTER_TRACE | **DEMONSTRATED** | `test_decorative_effects_never_enter_trace`: trace dict identical before/after all profile renders |

## Profile results

| Profile | Output | SHA-256 | Size | Frames |
|---------|--------|---------|------|--------|
| debug | `debug.gif` | `120233A49165AACE531A49C094D43B81A676ADA7F0B187384728206E4B04A52D` | 4316 | 220 |
| trails | `trails.gif` | `05BC7A8F3D1A31947327EB76EC77A4D01AB88B42256A6ED96394422BE5DE3F18` | 49156 | 120 |
| heatmap | `heatmap.gif` | `A19E0F2A570771EBBDDB5870456CF9D292153A2A72D215B3A02FE25A364B38A6` | 32149 | 120 |
| graph | `graph.gif` | `2CE70BCE5164466C8008A5072AD0AF2879295D6E33C7242EDBCE2A7936E378AC` | 65948 | 120 |
| orbit | `orbit.gif` | `725A4548B419AB01352F8E78BF7E9B9BCA0A1B64DC470319A8716FE2601F77F3` | 76384 | 120 |
| story | `story.gif` | `B1D2DF23C14625B3472067F80A161BD6192C0C6FD80CDDFB2C88201CDD49D1C6` | 34270 | 120 |
| cinematic | `cinematic.gif` | `96D3CE440E5496C64F011B34D7CBB8FA4C74EC9CCC671812076E2E59B20003D8` | 26032 | 120 |

All 7 GIFs derived from the SAME `execution.trace.json`. The VM was executed ONCE.
Canonical invariant: `TRACE != VIEW`.

## Profile verifications

| Verdict | Result |
|---------|--------|
| DEBUG_PROFILE_REGRESSION | **PASS** — debug GIF hash = legacy baseline `120233A4…` (byte-identical) |
| EXISTING_TRACE_RENDER | **DEMONSTRATED** — all profiles render from saved `.json` file without re-running VM |
| MULTIPLE_TRACE_PROJECTIONS | **DEMONSTRATED** — 7 distinct visual outputs from single trace |
| TRAILS_PROFILE | **PASS** — fading trails + interpolated segments + event pulses |
| HEATMAP_PROFILE | **PASS** — cumulative cell activity from real events (moves, traces, writes, spawns, deaths) |
| GRAPH_PROFILE | **PASS** — event type transition graph, circle layout, progressive reveal |
| ORBIT_PROFILE | **PASS** — abstract entity layout with event pulses |
| STORY_PROFILE | **PASS** — spec-driven labeled entities + caption bar |
| CINEMATIC_PROFILE | **PASS** — glow + interpolation + trails + persistence pulses |

## Determinism

| Verdict | Result | Method |
|---------|--------|--------|
| DETERMINISTIC_RENDER | **BYTE_DETERMINISTIC** | Each profile rendered twice; SHA-256 identical for all 7 profiles (story uses `render-spec.json`) |

GIF encoder (Pillow `save_all`) produces identical bytes given same frames + timing.
No metadata nondeterminism observed in this Pillow version.

## Tests

| Verdict | Result |
|---------|--------|
| ALL_EXISTING_TESTS | **PASS** — 24/24 original tests unchanged |
| NEW_RENDER_TESTS | **PASS** — 18/18 new profile tests pass |
| TOTAL | **42/42 PASS** |

New test coverage:
- debug regression (byte-identical legacy match)
- trace never mutated by any profile render
- trace hash unchanged before/after
- render from existing trace file (offline)
- deterministic repeated render (all profiles)
- invalid profile rejected
- malformed spec rejected
- unknown event type in spec rejected
- unknown entity in event_styles rejected
- duplicate entity id rejected
- bad schema_version rejected
- malformed JSON rejected
- interpolation is presentation-only
- persistence does not alter event count
- decorative effects never enter trace
- zero-event trace edge case
- one-event trace edge case
- multi-particle trace (8 particles with SPLIT)
- all profiles produce output with >= 2 frames

## Files changed

| File | Action |
|------|--------|
| `flow/render/spec.py` | **NEW** — RenderSpec + validation |
| `flow/render/profiles.py` | **NEW** — 7 profile renderers |
| `flow/render/__init__.py` | Updated exports |
| `flow/cli/flow_cli.py` | Added `--profile`, `--spec` to `flow render` |
| `tests/test_profiles.py` | **NEW** — 18 tests |
| `docs/RENDER_ARCHITECTURE_AUDIT.md` | **NEW** — architecture audit |
| `evidence/render-profiles/` | **NEW** — golden demo artifacts |
