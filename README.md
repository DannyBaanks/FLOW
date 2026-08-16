# FLOW

**FLOW v0.1** — Image-as-Vector-Field Esolang VM.

El programa **ES una imagen** (PNG). Los canales R/G codifican un campo vectorial
continuo; el canal B codifica datos + instrucciones. Partículas fluyen por el
campo, ejecutando instrucciones al muestrear el canal B bajo su posición.

## Instalación

```bash
pip install -e .
```

Requiere: `pillow>=10`, `numpy>=1.24` (Python 3.10+).

## Ejecución

```bash
# Generar ejemplos
flow run examples/hello_flow.png --max-ticks 200 --seed 42 --trace trace.json
flow run examples/vortex.png --max-ticks 500 --seed 42 --trace trace.json

# Replay determinista (verifica reproducción exacta)
flow replay examples/vortex.png --trace trace.json --output-trace trace_replay.json

# Comparar traces (proyección semántica)
flow trace-diff trace.json trace_replay.json

# Validar schema de trace
flow validate trace.json
```

## Determinismo

- `--seed` controla `RAND` (instr 22). Mismo programa + misma seed = trace **idéntico**.
- Sin `RAND` ejecutado: distintas seeds producen trace **idéntico**.
- Con `RAND` ejecutado: distintas seeds producen traces **diferentes**.
- `replay` re-ejecuta y compara con `trace-diff` (proyección semántica: seed excluida).

## Salidas legacy (opcionales)

`--output prefix` genera:
- `prefix_b.png` — campo B final
- `prefix_trace.png` — capa TRACE (si se usó instr 24)
- `prefix.csv` — log tick-by-tick

## Tests

```bash
python -m pytest tests/ -v
```

16 tests: 8 unitarios del VM original + 8 de determinismo M1 (trace, replay, diff, validate).

## Ejemplos incluidos

| Archivo | Descripción |
|---------|-------------|
| `examples/hello_flow.png` | Partícula fluye derecha, INC×5 → WRITE → HALT |
| `examples/vortex.png` | 8 partículas en vórtice con SPLIT + TRACE |

## Arquitectura

```
flow.core      # VM pura (FlowVM, Particle, instrucciones)
    ↓
flow.runtime   # ExecutionTrace (observación pura, sin re-ejecutar lógica)
    ↓
flow.cli       # run / replay / trace-diff / validate
    ↓
flow.render    # (M2: image/gif renderers consumen ExecutionTrace)
```

## License

Dominio público / CC0.