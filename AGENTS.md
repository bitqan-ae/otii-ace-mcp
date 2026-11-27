# AGENTS.md

How to work on this repository, for coding agents and contributors. The rules themselves are
the design constraints in [docs/contributor/design.md](docs/contributor/design.md); this page
summarises them and says where things are.

## What this is

`otii-usb` (repository `otii-ace-mcp`): Python driver, command line, interactive shell and MCP
server for the **Qoitech Otii Ace Pro** over USB. Only the Otii Ace Pro with firmware 3.2.5 is
tested and operated (`REQ-SAFE-DEV-001`). Apache-2.0.

## Where things are

```
src/otii_usb/
  transport/      Transport ABC, SerialTransport (stdlib, flock), USB discovery
  protocol/       constants, framing, packets, Session
  device/         properties, channels, support (tested devices), models, OtiiDevice
  measurement/    Recorder, sinks, Binner, CSV, statistics
  report/         PlotSink, PNG rendering
  simulator/      SimulatedOtii (a Transport emulating the device)
  interfaces/     commands, console, gateway, lifecycle, monitor, cli, interactive, banner,
                  mcp_server
tests/            offline suite (simulator); tests/hardware/ needs --hardware
docs/contributor/ design.md (requirements, constraints, review guideline), protocol.md
docs/images/      SVG originals; png/ holds the rendered copies
docs/tools/       protocol_figures.py regenerates the protocol figures
```

## Setup and commands

```sh
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
python -m pytest                          # offline suite, no hardware
python -m pytest --hardware tests/hardware  # read-only checks on an attached Otii Ace Pro
otii-usb --simulate -i                    # interactive shell against the simulator
otii-usb --debug status                   # every frame as hex
python docs/tools/protocol_figures.py     # regenerate protocol SVGs and PNGs
```

## Rules in short

Identifiers (`REQ-…`, `DC-…`): never reused, a new one when the meaning changes, rows in number
order, cited as code. Full rules: [design.md, Identifiers](docs/contributor/design.md#identifiers).

**Hardware safety**
- The device powers a device under test. Prefer read-only operations (`status`, `measure`);
  switch outputs or change setpoints only when asked, and leave the outputs off afterwards.
- Keep the safe state at start and on every exit (`REQ-SAFE-OUT-001`, `-002`); the shutdown
  must not be interruptible (`REQ-SAFE-OUT-003`) and must not depend on the event loop
  (`DC-ARCH-RUN-002`). Signal handlers do no I/O and take no lock (`DC-HOST-SIG-003`).
- Writable settings are a whitelist (`REQ-SAFE-DEV-003`); add one only after verifying it on
  hardware, with a read-back check (`REQ-SAFE-DEV-002`).
- Send only the commands documented in [protocol.md](docs/contributor/protocol.md)
  (`REQ-SAFE-DEV-007`); learn new details from `--debug` captures and document them there.
- One process owns the serial port; if it is busy, ask before stopping the holder.

**Changes** (`DC-TOOL-DOC-007`, `-008`)
- Start with the requirement row; change documents in the same commit as the code.
- Commit messages say what and why and cite identifiers; no tool or assistant attribution.

**Documents** (`DC-TOOL-DOC-001` to `-006`, `-009`, `-010`)
- README for users; `docs/contributor/` and this page for contributors.
- A page opens with its purpose and audience; it says what is, not what was.
- One fact, one place; link instead of repeating.
- No serial numbers, certificates, usernames, paths, host names or addresses in any file.
- Drawings: SVG plus a rendered PNG, committed together.
- A file named in a Markdown page is a link with its name as the link text
  (`DC-TOOL-DOC-010`).
- Every source file carries the Apache-2.0 header with its description and identifiers;
  configuration files (TOML, YAML, JSON, [requirements.txt](requirements.txt)) carry none.

**Python** (`DC-TOOL-PY-001`, `-002`): Python ≥ 3.12; numpy for sample data; dependencies in
[`pyproject.toml`](pyproject.toml) and [`requirements.txt`](requirements.txt); warnings are errors in tests.

**Tests** (`DC-TOOL-TEST-001` to `-006`)
- Write only in the test's temporary directory; every test can fail.
- Wait for events, not for fixed times; run new or failed tests alone first.
- Hardware tests live in `tests/hardware/` and skip themselves without `--hardware`.

**Reviews** (`DC-TOOL-REV-001`): every change is reviewed against the
[code review guideline](docs/contributor/design.md#code-review-guideline); findings ranked,
each with file and line, a fix and a certainty (certain, likely, unsure).
