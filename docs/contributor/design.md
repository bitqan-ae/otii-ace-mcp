# otii-usb — design

This document collects the design requirements of `otii-usb` with unique IDs, and describes the
software design as implemented. Safety requirements are prefixed `SAF-`, software design
requirements `SW-`. Code, tests and commit messages refer to these IDs; when behaviour changes,
update the requirement here in the same change.

## 1. Scope

`otii-usb` controls a Qoitech Otii power analyzer over USB and makes it usable for automation:
a Python API, a one-shot CLI for scripts and CI, an interactive shell for people, and an MCP
server for agents. It powers a device under test (DUT) and measures its voltage, current and
power at 50 kS/s.

**Tested hardware:** Otii Ace Pro (device type `Ace`, hardware rev 2, firmware 3.2.5,
bootloader 3.2.2, protocol 3). No other device or firmware line has been tested; the driver
refuses to operate anything else (SAF-008).

## 2. Architecture

![Architecture](images/architecture.svg)

| Package | Responsibility |
|---|---|
| `transport/` | Moving bytes: `Transport` ABC, `SerialTransport` (raw CDC-ACM, exclusive `flock`), USB discovery via sysfs. |
| `protocol/` | Wire format without device semantics: frames, typed packets, `Session` (transactions, listeners, keepalive, cancellation, progress). |
| `device/` | Device model: typed property specs and codecs, channel table, supported-device table, `OtiiDevice` facade. |
| `measurement/` | `Recorder` streams packets into sinks; `Binner`, CSV sink, array/statistics sinks, `TeeSink`. |
| `report/` | `PlotSink` and PNG rendering with matplotlib (object API, thread-safe). |
| `simulator/` | `SimulatedOtii`, a `Transport` that emulates the device for tests and `--simulate`. |
| `interfaces/` | Command catalogue, console rendering and progress bars, `DeviceGateway`, lifecycle/signal handling, CLI, interactive shell, MCP server. |

Dependencies point downwards only (SW-001). The simulator sits beside the serial transport: every
layer above `transport/` runs unchanged against either.

### 2.1 Recording data flow

```
Otii ──1 ms packets──▶ Session ──DataPacket──▶ Recorder/_PacketRouter ──numpy arrays──▶ SampleSink
                                                                                         ├─ ArraySink  → Recording (memory)
                                                                                         ├─ StatsSink  → running statistics
                                                                                         ├─ CsvSink    → Binner → CSV rows
                                                                                         └─ PlotSink   → Binner → PNG
```

One recording can feed several sinks through `TeeSink`, e.g. CSV and PNG in parallel. Samples
are decoded with `numpy.frombuffer` into float64 SI units; CSV writing streams to disk, so memory
does not grow with the recording length.

### 2.2 Concurrency

* The CLI runs in the main thread; Ctrl-C raises `KeyboardInterrupt` at the next bytecode.
* The MCP server runs tools in worker threads (`anyio.to_thread`). `DeviceGateway` serialises
  calls with a lock; every call opens its own connection and binds it to a cancel event.
* Shutdown never relies on the event loop: a signal starts a dedicated thread that sets the
  cancel event, waits for the port, applies the safe state and exits the process (SAF-004).

### 2.3 Safety lifecycle

![Safety lifecycle](images/safety-lifecycle.svg)

## 3. Safety requirements

| ID | Requirement | Implementation | Verification |
|---|---|---|---|
| SAF-001 | When the MCP server or the interactive shell starts, the main output and the expansion-port output are switched off, the voltage setpoint is set to 5 V and the current limit to 500 mA (configurable) before any other operation. If no device is attached, this happens on the first connection, before the first command. | `DeviceGateway.ensure_safe`, `_apply_safe` in every `call`; `OtiiDevice.apply_safe_defaults` | `test_safe_state_applied_before_first_tool`, `test_safe_state_applied_before_first_command`, hardware check |
| SAF-002 | On every way of ending (Ctrl-C, SIGTERM, SIGHUP, client disconnect, `exit`, end of input) the device is left with recording stopped, all outputs off and the startup defaults restored. | `OtiiDevice.shutdown`, `safe_shutdown`, `DeviceGateway.shutdown`, `SafeExit` | `test_mcp_server_signal_shutdown[*]`, `test_mcp_server_client_disconnect_shutdown`, `test_session_loop_exits_with_outputs_off`, hardware check |
| SAF-003 | The shutdown sequence cannot be interrupted by further signals. | `signals_shielded`, `SafeExit` ignores repeated signals | code review, `test_cli_signal_during_measure` |
| SAF-004 | Outputs go off promptly after a stop request even while a long operation runs or the server is blocked on stdin: blocking device I/O is cancellable within 50 ms; the server exits within 3 s. | `Session` cancel event polled every 50 ms; `SafeExit` thread independent of the event loop | `test_shutdown_cancels_running_tool`, `test_mcp_server_signal_shutdown[*-1.5]` (asserts < 3 s); measured 10–60 ms on hardware |
| SAF-005 | Every safety-relevant write is verified by reading it back; a mismatch is an error. | `set_output`, `_verify` in `set_voltage_setpoint`, `set_current_limit` | `test_set_output`, `test_apply_safe_defaults` |
| SAF-006 | Only properties explicitly marked writable can be set; all others are refused before anything is sent. | `PropertySpec.writable`, `PropertyClient.set` | `test_read_only_property_is_refused` |
| SAF-007 | Setpoints are range-checked: the voltage against the device's reported maximum, the current limit must be positive; MCP tool parameters are clamped (measure ≤ 10 s, record ≤ 600 s, off time 0.05–60 s). | `set_voltage_setpoint`, `set_current_limit`, `mcp_server` tools | `test_voltage_above_device_maximum_rejected` |
| SAF-008 | Only tested devices are operated. Identification (device type, protocol, firmware) only reads; an unsupported device is refused before any write, and the MCP server and the interactive shell do not start (exit 2). | `device/support.py`, `OtiiDevice.__init__` | `TestUnsupportedDevice`, `test_unsupported_device_refused` |
| SAF-009 | A device is used by one process at a time. | exclusive `flock` in `SerialTransport` | hardware check (`PortBusyError`) |
| SAF-010 | `power_cycle` always ends with the main output on; this is stated in CLI help, MCP tool description and documentation. | `OtiiDevice.power_cycle` | `test_power_cycle_from_off_ends_on` |
| SAF-011 | MCP tools are annotated: reads `read_only_hint`, output switching `destructive_hint`, so clients ask before switching power. | `mcp_server.create_server` | `test_server_lists_tools`, review |
| SAF-012 | No command IDs other than those listed in §5 are sent to hardware. | `protocol/constants.py` | review |
| SAF-013 | A recording is always stopped and its channels disabled, also when interrupted; a leftover recording is stopped by the safe shutdown. | `Recorder._streaming` (finally), `OtiiDevice.stop_recording` | `test_recording_cleans_up`, `test_device_shutdown_stops_leftover_recording` |
| SAF-014 | SIGTERM and SIGHUP are handled like Ctrl-C. SIGINT works even if the process inherited it as ignored (background jobs). | `termination_as_interrupt`, `SafeExit`, `run_interactive` | `test_*_signal_*[*-True-*]` |
| SAF-015 | Files written on behalf of MCP clients stay inside the configured output directory (plain names only). | `OutputDirectory` | `TestMcpOutputDirectory` |
| SAF-016 | In the interactive shell, Ctrl-C during a command stops it and switches the outputs off; the prompt always shows the main output state. | `InteractiveShell._run`, `_prompt` | `test_ctrl_c_during_command_switches_outputs_off`, `test_output_state_tracked_for_prompt` |
| SAF-017 | The CLI exits with 130 after an interrupt, having switched the outputs off. | `cli.main` | `test_cli_ctrl_c_switches_outputs_off`, `test_cli_signal_during_measure` |

## 4. Software design requirements

| ID | Requirement | Implementation | Verification |
|---|---|---|---|
| SW-001 | Layered packages with downward dependencies only (§2). | package layout | review |
| SW-002 | The byte transport is an abstraction (`Transport`); the serial implementation uses only the standard library (`termios`, `select`, `fcntl`). | `transport/` | `SilentTransport` in tests |
| SW-003 | Framing: `u32 magic | u16 length | u16 sum16 | payload`; the decoder accepts arbitrary chunks and resynchronises after garbage or checksum errors. | `protocol/framing.py` | `TestFraming` |
| SW-004 | `Session` matches responses by transaction id, dispatches other packets to listeners, sends keepalives every 0.5 s while streaming, reports progress and supports cancellation. | `protocol/session.py` | `TestSession` |
| SW-005 | Device properties are typed (`PropertySpec[T]` with a `Codec[T]`) and catalogued in one place (`Properties`). | `device/properties.py` | `TestCodecs` |
| SW-006 | Channel capabilities are read from the device (`chinfo`), not hard-coded. | `device/channels.py` | `TestChannels` |
| SW-007 | All sample data is numpy float64 in SI units; no per-sample Python loops. | `ChannelInfo.decode_samples`, `Binner` | `test_record_returns_numpy` |
| SW-008 | Recordings stream into `SampleSink`s; several sinks can be fed from one recording. | `measurement/` | `test_record_tool[*]` |
| SW-009 | CSV: header `time_s,voltage_v,current_a,power_w`; rows averaged to a selectable rate (default 1 kHz) or every sample (`--raw`); time from packet sequence numbers so gaps stay visible; statistics over the full-rate samples; the file is complete when interrupted. | `CsvSink`, `Binner` | `tests/test_csv.py` |
| SW-010 | PNG: voltage, current and power panels over a shared time axis, mean line with min/max band, the device model and serial number in the title, recording details below it. | `report/plot.py` | `test_record_tool[*]`, visual check |
| SW-011 | A simulator implements the same `Transport` interface; the whole test suite runs without hardware. | `simulator/` | CI |
| SW-012 | One-shot CLI with `--json` output and exit codes 0 (ok), 1 (device error), 2 (usage / unsupported device), 130 (interrupted). | `interfaces/cli.py` | `TestCli` |
| SW-013 | Interactive shell (`-i`): ASCII-art banner, coloured prompt with output state, syntax highlighting, completion of commands/options/paths, persistent history, auto-suggestion; built on prompt_toolkit and rich. | `interfaces/interactive.py`, `banner.py` | `tests/test_interactive.py` |
| SW-014 | One command catalogue feeds the CLI, the interactive shell and completion; every command has help text and examples. | `interfaces/commands.py` | `test_every_command_has_help_with_examples` |
| SW-015 | Operations with a known duration show a progress bar with an ASCII spinner, elapsed and remaining time, on terminals only (stderr), never in `--json` output or pipes. | `console.progress_bar` | visual check |
| SW-016 | MCP server tools: `status`, `measure`, `record` (CSV and/or PNG), `set_output`, `power_cycle`; one connection per call. | `interfaces/mcp_server.py` | `test_server_lists_tools` |
| SW-017 | Python ≥ 3.12; dependencies: numpy, matplotlib, rich, prompt_toolkit, mcp. | `pyproject.toml`, `requirements.txt` | CI |
| SW-018 | CI runs the test suite and a simulator smoke test on Python 3.12, 3.13 and 3.14. | `.github/workflows/ci.yml` | GitHub Actions |
| SW-019 | Every source file starts with the Apache-2.0 copyright header (SPDX identifier) and a description of what the file does. | all files | review |

## 5. Protocol reference (verified on the Otii Ace Pro, firmware 3.2.5)

```
frame    : u32 magic 0x7fb52ca3 | u16 len | u16 sum16(payload) | payload     (both directions)
request  : u32 txn | u32 cmd | args
response : u32 0xff99030e | u32 txn | u32 errcode | data        (errcode 0 = ok)
update   : u32 0xff2ff2ce ...          alert: u32 0xffc593ca ...
data     : u32 0xff2a8369 | u32 ms_seq | { u16 ch | u16 n | u32 sps | n x 4B }*
hello    : u32 protocol | u32 max_payload | char[4] device type (reversed) | u32 feature bits
```

| cmd | meaning | args |
|---|---|---|
| `0x0a` | ping / keepalive | 8 × `00` |
| `0x14` | hello | — |
| `0x64` | get property | `[u32 id][u32 0]` |
| `0x66` | set property | `[u32 id][4-byte value]` |
| `0x78` | channel enable/disable | `[u32 ch][u32 1/0]` |
| `0x7c` / `0x7e` | recording start / stop | — |

| property | id | encoding |
|---|---|---|
| name / fwversion / bootversion | `0x01` / `0x81` / `0x87` | string |
| hwversion / serial (uid) | `0x80` / `0x82` | string |
| main output | `0x09` | i32 bool |
| main voltage setpoint | `0x0b` | i32 µV; maximum at `0x80b` (25 V) |
| current limit | `0x100c` | f32 A (`0x0c` reads i32 mA) |
| expansion output voltage | `0x34` | i32 µV, 0 = off |
| channel info | `0x8d` | 16-byte records `[u16 ch][u16 flags][u8][3 pad][u32 sps][f32 scale]` |

ID modifiers: `|0x400` min, `|0x800` max, `|0xc00` enum, `|0x1000` float32, `|0x1400` step.
Unsupported ids answer errcode `0xfffffffd`. Channel info flags: bit 15 float32 samples, bit 14
multiply by scale, low byte kind (0 analog, 1 UART, 2 digital). Main channels: `mc 0` (A),
`mv 1` (V), `mp 6` (W). Current ranging is automatic; the range override (`0x3c`) is never written.

Anything not listed here is unverified. New protocol details are confirmed with a `--debug`
capture from real hardware (read-only first), then added here and to the simulator.
