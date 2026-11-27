# AGENTS.md

Guidance for coding agents and contributors working in this repository.

**Read [docs/design.md](docs/design.md) first.** It holds the numbered safety (`SAF-xxx`) and
software design (`SW-xxx`) requirements, the architecture, and the protocol reference. Every
change must keep those requirements true; when behaviour changes, update the requirement in the
same change and reference its ID in the commit message.

## What this is

`otii-usb` (repository `otii-ace-mcp`): Python driver, CLI, interactive shell and MCP server for
the **Qoitech Otii Ace Pro** over USB. Only the Otii Ace Pro (firmware 3.2.x) is tested and
supported (SAF-008). Licensed Apache-2.0.

## Layout

```
src/otii_usb/
  errors.py          exception hierarchy (everything derives from OtiiError)
  transport/         Transport ABC, SerialTransport (stdlib, flock), USB discovery
  protocol/          constants, framing, packets, Session (txn, listeners, keepalive, cancel, progress)
  device/            properties (PropertySpec[T], codecs, catalogue), channels, support (tested
                     devices), models (value objects), otii.py (OtiiDevice facade)
  measurement/       Recorder, sinks (array, stats, tee), Binner, CSV sink, statistics
  report/            PlotSink + matplotlib PNG rendering
  simulator/         SimulatedOtii: a Transport emulating the device
  interfaces/        commands (catalogue + help/examples), console (rich output, progress bars),
                     gateway (serialised, cancellable access + safe state), lifecycle (signals,
                     safe exit), cli.py, interactive.py, banner.py, mcp_server.py
tests/               pytest suite, hardware-free (simulator)
docs/design.md       requirements, architecture, protocol reference; images in docs/images/
```

Dependencies point downwards: `interfaces -> device / measurement / report -> protocol ->
transport`. `simulator` uses `device` + `protocol`.

## Setup and commands

```sh
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
python -m pytest                               # must stay green, no hardware needed
otii-usb --simulate -i                         # interactive shell against the simulator
otii-usb status | measure | record | output on|off | power-cycle | safe
otii-usb --debug measure                       # every frame as hex
otii-usb-mcp [--port P] [--voltage 5] [--current-limit 0.5] [--output-dir DIR]
```

## Hardware safety — read before running anything

- This drives **real hardware that powers a device under test**. Switching the main output
  (`output on`, `power-cycle`, `set_output`) can damage or reset it.
- Default to **read-only** operations (`status`, `measure`). Switch outputs or change setpoints
  only when asked, and leave the outputs off afterwards.
- Keep the safe-state behaviour (SAF-001, SAF-002): outputs off and 5 V / 500 mA at start and
  exit of the MCP server and the interactive shell, and on Ctrl-C/SIGTERM/SIGHUP.
- Writable settings are an explicit whitelist (`writable=True`, SAF-006); add one only after
  verifying the encoding on hardware, with a read-back check (SAF-005).
- Never send command IDs that are not in the protocol reference (SAF-012).
- Only one process can own the serial port; if it is busy, ask before stopping the holder.

## Conventions

- Python ≥ 3.12, PEP 695 generics, frozen/slotted dataclasses for value objects.
- Sample data is numpy float64 in SI units; no per-sample Python loops.
- New device features: wire constant (`protocol/constants.py`) → `PropertySpec`
  (`device/properties.py`) → `OtiiDevice` method → simulator support → tests → a `Command` in
  `interfaces/commands.py` (help text and examples are required) and, if useful, an MCP tool.
- MCP tools: `read_only_hint` for reads, `destructive_hint` for anything that switches power,
  clamp numeric inputs, write files only through `OutputDirectory`.
- Every source file starts with the Apache-2.0 copyright header and a description of what the
  file does (SW-019); keep that description current when the file changes.
- Commit messages describe the change and reference requirement IDs; no tool or assistant
  attribution in commits or source files.
