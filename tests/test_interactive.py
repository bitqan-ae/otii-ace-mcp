# test_interactive.py: the interactive shell
#
# Copyright (c) 2026 Milosch Meriac <meriac@bitqan.ae>
#    developed at Bitqan Systems Design (bitqan.ae)
#         From Idea to Manufacturable Product
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# SPDX-License-Identifier: Apache-2.0
#
# Commands, recordings with their own plot titles in sequence
# (REQ-MEAS-REC-005), help, error handling, the output state in the prompt, Ctrl-C
# during a command (REQ-SAFE-OUT-006), the lexer's highlighting, completion, a
# full scripted session through prompt_toolkit that must end with the outputs
# off (REQ-SAFE-OUT-002), and refusal of unsupported devices
# (REQ-SAFE-DEV-001).

import io

import pytest
from prompt_toolkit.document import Document
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from otii_usb import OtiiDevice, SafeDefaults
from otii_usb.interfaces.banner import ascii_art
from otii_usb.interfaces.console import make_console
from otii_usb.interfaces.commands import COMMANDS
from otii_usb.interfaces.gateway import DeviceGateway
from otii_usb.interfaces.interactive import (CommandLexer, InteractiveShell, build_completer,
                                             build_shell_parser)
from otii_usb.simulator import SimulatedOtii, SimulationConfig


@pytest.fixture
def sim():
    return SimulatedOtii(SimulationConfig(sample_rate=5_000))


@pytest.fixture
def shell(sim):
    out = io.StringIO()
    gateway = DeviceGateway(lambda: OtiiDevice.connect(sim), SafeDefaults())
    sh = InteractiveShell(gateway, version="test", console=make_console(file=out, width=120),
                          history=InMemoryHistory())
    sh.out = out
    return sh


def output(shell) -> str:
    text = shell.out.getvalue()
    shell.out.truncate(0)
    shell.out.seek(0)
    return text


def test_banner_art_is_rectangular():
    rows = ascii_art("OTII ACE")
    assert len(rows) == 5 and max(map(len, rows)) > 40


def test_measure_and_status(shell):
    shell.execute("measure -t 0.02")
    assert "mv" in output(shell)
    shell.execute("status")
    text = output(shell)
    assert "Otii Ace Pro" in text and "current limit" in text


def test_safe_state_applied_before_first_command(shell, sim):
    sim.state.output_enabled = True
    shell.execute("status")
    assert sim.state.output_enabled is False and sim.state.voltage_setpoint == pytest.approx(5.0)


def test_output_state_tracked_for_prompt(shell):
    shell.execute("output on")
    assert shell.output_on is True
    assert ("class:prompt.on", "● ON") in shell._prompt()
    shell.execute("output off")
    assert shell.output_on is False


def test_record_both_outputs(shell, tmp_path):
    shell.execute(f"record -t 0.05 --csv {tmp_path}/a.csv --png {tmp_path}/a.png")
    assert (tmp_path / "a.csv").exists() and (tmp_path / "a.png").exists()


def test_record_titles_in_sequence(shell, tmp_path):
    from PIL import Image
    for n, title in enumerate(("Board A, idle", "Board A, boot")):
        shell.execute(f'record -t 0.05 --png {tmp_path}/{n}.png --title "{title}"')
        with Image.open(tmp_path / f"{n}.png") as image:
            assert image.text["Title"] == title


def test_help_lists_every_command(shell):
    shell.show_help()
    text = output(shell)
    assert all(c.name in text for c in COMMANDS)


def test_help_for_command_shows_examples(shell):
    shell.execute("help record")
    text = output(shell)
    assert "examples:" in text and "record -t 60 --csv run.csv" in text


def test_unknown_command_and_bad_arguments_do_not_crash(shell):
    shell.execute("frobnicate")
    shell.execute("measure -t -5")
    shell.execute('record "unterminated')
    shell.execute("status")
    assert "Otii Ace Pro" in output(shell)


def test_ctrl_c_during_command_switches_outputs_off(shell, sim, monkeypatch):
    shell.execute("output on")
    assert sim.state.output_enabled

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt
    monkeypatch.setattr(OtiiDevice, "measure", interrupted)
    shell.execute("measure -t 5")
    assert sim.state.output_enabled is False
    assert shell.output_on is False


def test_lexer_styles():
    tokens = CommandLexer().lex_document(Document("record -t 10 --csv run.csv"))(0)
    styles = {text: style for style, text in tokens if text.strip()}
    assert styles == {"record": "class:command", "-t": "class:option", "10": "class:number",
                      "--csv": "class:option", "run.csv": "class:arg"}
    assert CommandLexer().lex_document(Document("bogus"))(0)[0][0] == "class:unknown"


def test_completer_offers_commands_and_options():
    completer = build_completer(build_shell_parser()[1])
    words = lambda text: {c.text for c in completer.get_completions(Document(text), None)}
    assert {"record", "measure", "power-cycle", "help", "exit"} <= words("")
    assert {"--csv", "--png", "--raw", "--rate"} <= words("record --")
    assert words("output ") >= {"on", "off"}


def test_session_loop_exits_with_outputs_off(sim):
    out = io.StringIO()
    gateway = DeviceGateway(lambda: OtiiDevice.connect(sim), SafeDefaults())
    with create_pipe_input() as pipe:
        pipe.send_text("output on\rstatus\rexit\r")
        shell = InteractiveShell(gateway, version="test", console=make_console(file=out, width=120),
                                 history=InMemoryHistory(),
                                 prompt_io={"input": pipe, "output": DummyOutput()})
        assert shell.run() == 0
    text = out.getvalue()
    assert "Otii Ace Pro" in text and "safe state" in text
    assert "defaults restored" in text
    assert sim.state.output_enabled is False


def test_unsupported_device_refused(tmp_path):
    sim = SimulatedOtii(SimulationConfig(device_type="Arc"))
    gateway = DeviceGateway(lambda: OtiiDevice.connect(sim), SafeDefaults())
    shell = InteractiveShell(gateway, version="test", console=make_console(file=io.StringIO()),
                             history=InMemoryHistory())
    assert shell.run() == 2


def test_history_is_persistent(monkeypatch, tmp_path):
    from prompt_toolkit.history import FileHistory
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    sim = SimulatedOtii()
    shell = InteractiveShell(DeviceGateway(lambda: OtiiDevice.connect(sim), SafeDefaults()),
                             version="t", console=make_console(file=io.StringIO()))
    history = shell._file_history()
    assert isinstance(history, FileHistory)
    assert (tmp_path / "otii-usb").is_dir()


def test_output_tripped_event_updates_prompt(shell):
    from otii_usb.interfaces.monitor import MonitorEvent
    shell.output_on = True
    shell.on_event(MonitorEvent("critical", "output_tripped", "switched off by the device"))
    assert shell.output_on is False


def test_exit_code_1_when_outputs_cannot_be_switched_off(sim, monkeypatch):
    gateway = DeviceGateway(lambda: OtiiDevice.connect(sim), SafeDefaults())
    monkeypatch.setattr(gateway, "shutdown", lambda: None)
    with create_pipe_input() as pipe:
        pipe.send_text("exit\r")
        shell = InteractiveShell(gateway, version="t", console=make_console(file=io.StringIO()),
                                 history=InMemoryHistory(),
                                 prompt_io={"input": pipe, "output": DummyOutput()},
                                 poll_interval=None)
        assert shell.run() == 1


def test_file_error_keeps_the_session(shell, tmp_path):
    shell.execute(f"record -t 0.02 --csv {tmp_path}/missing/x.csv")
    shell.execute("status")
    assert "Otii Ace Pro" in output(shell)
