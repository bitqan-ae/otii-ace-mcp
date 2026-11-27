# protocol_figures.py: draws the figures of docs/contributor/protocol.md as
# SVG and renders PNG copies for README.md
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
# Usage (from the repository root, with the venv active):
#
#   python docs/tools/protocol_figures.py
#
# The SVG files in docs/images/ are the originals; PNG copies at twice the
# size go to docs/images/png/ (needs rsvg-convert from librsvg). All byte
# values come from a capture of a real Otii Ace Pro (firmware 3.2.5, main
# output off) embedded below, and every length and checksum is computed with
# the driver's own framing code, so the figures cannot drift from the
# implementation. Colours are consistent across figures: one colour per kind
# of field (see FIELDS). REQ-DOC-PROT-001, DC-TOOL-DOC-005.

import shutil
import struct
import subprocess
from dataclasses import dataclass
from html import escape
from pathlib import Path

from otii_usb.protocol.framing import checksum, encode_frame

OUT = Path(__file__).resolve().parents[1] / "images"
SANS = "Segoe UI, Helvetica, Arial, sans-serif"
MONO = "DejaVu Sans Mono, Menlo, Consolas, monospace"
INK, MUTED, LINE = "#111827", "#6b7280", "#d1d5db"

# ---------------------------------------------------------------- capture (real device)

# GET_PROPERTY fwversion request (host -> device), as sent: frame with header
CAP_GET_FW = bytes.fromhex("a32cb57f1000e700020000006400000081000000" "00000000")
# HELLO response payload (device -> host)
CAP_HELLO_RESP = bytes.fromhex("0e0399ff01000000000000000300000020800000206563413f000000")
# first data packet payload after RECORD_START (channels mc, mv, mp enabled)
CAP_DATA = bytes.fromhex("69832aff000000000000320050c30000dd6da6b49d802db4882772b48ed6abb4d5e3c1b3b12405b2"
    "c6eea3b449119db4e1b218b4ad5076b31241acb390b156b3e5f78ab3f28261b487b60eb44b0e88b4"
    "21a011b40078ffb355beb3331e1003b4411a5cb4993b3bb467f8c3341861c9b4812995b417cea5b4"
    "7de4a2b42dba04b48a9139b4b12405b1edcc8bb49d802db4d1533334c781c7b4cd1f21b5cec3a4b4"
    "8052f1b32dba8434ad50f6b336b5cc33731237b42e2be8b3fa0c46b41ca6bbb41f81e6b3272d8b34"
    "e442a733e73f92b40d6996b49ccbc9b40100320050c30000926b8c3bb4178d3b65348d3b5aa98e3b"
    "e8198e3b49538e3b926b8c3b1291903b26a78d3bb822923b926b8c3b80158c3b99368e3b6fbf8b3b"
    "ecd98a3b926b8c3b1fdc8b3bb4178d3bcc388f3be14e8c3b1c1c8f3b26a78d3b6fbf8b3b3bbd8a3b"
    "65348d3b07bb893b53de8c3b42888c3bd7c38d3bad4c8b3b31328c3b73ca903b04fb8c3becd98a3b"
    "15518d3bcc388f3b04fb8c3be14e8c3b9cf68a3b2a678a3b04fb8c3b8ba08a3ba681893b53de8c3b"
    "f2a48c3b53de8c3befe48f3b1fdc8b3b6fbf8b3bcc388f3b0600320050c300000794b6b0ce3f3fb0"
    "559185b06085bfb0fe3fd7af560b14aeedd6b3b07665b1b073fc28b06a9b8cafeff7bcaf45f66aaf"
    "49669aaf783576b0c2cf1ab0f94195b0521e1fb0bfcc0cb0651ec92f65aa0fb0ab1576b026344fb0"
    "d6f4d5306446dab0c78ca4b0d968b2b0d744b3b0ebb811b03d864db09ae510adf51e99b01b4344b0"
    "59834530946bd8b018e331b1dc5bb8b0bee504b06c7d9130c5b405b05b58dd2f32a349b0c071fbaf"
    "7ec254b09c83ceb04346fdaf1f2b9930e207bc2fe6cc9fb00737a4b003cbe1b0")
# first property update payload (only the first 96 bytes are drawn)
CAP_UPDATE = bytes.fromhex("cef22fff0000000002000c00000001000000000000000000020008000100000075c28c3b02000800"
    "00000000f2c5f6b30200080006000000f5c907b002000800100000006b6f9f400200080011000000"
    "7af098410200080013000000c08a9841")
# one channel-info record (channel 0) and the digital-input record from chinfo
CAP_CHINFO_MC = bytes.fromhex("0000008032000000" "50c3000000000000")
CAP_CHINFO_GPI = bytes.fromhex("1600020000000000" "0000000000000000")


# ---------------------------------------------------------------- colours per field kind

@dataclass(frozen=True)
class Kind:
    fg: str
    bg: str
    name: str


FIELDS = {
    "magic": Kind("#475569", "#e2e8f0", "frame magic"),
    "length": Kind("#1d4ed8", "#dbeafe", "payload length"),
    "checksum": Kind("#be123c", "#ffe4e6", "checksum"),
    "type": Kind("#6d28d9", "#ede9fe", "packet type"),
    "txn": Kind("#b45309", "#fef3c7", "transaction id"),
    "seq": Kind("#b45309", "#fef3c7", "sequence number"),
    "cmd": Kind("#0e7490", "#cffafe", "command"),
    "status": Kind("#15803d", "#dcfce7", "error code"),
    "arg": Kind("#047857", "#d1fae5", "argument"),
    "data": Kind("#374151", "#f3f4f6", "data"),
    "channel": Kind("#047857", "#d1fae5", "channel id"),
    "count": Kind("#c2410c", "#ffedd5", "sample count"),
    "rate": Kind("#4d7c0f", "#ecfccb", "sample rate"),
    "sample": Kind("#0f766e", "#ccfbf1", "sample"),
    "sample2": Kind("#0369a1", "#e0f2fe", "sample"),
    "flags": Kind("#7e22ce", "#f3e8ff", "flags"),
    "unknown": Kind("#9ca3af", "#f9fafb", "unknown / padding"),
}


class Svg:
    def __init__(self, width: int, title: str) -> None:
        self.width = width
        self.title = title
        self.parts: list[str] = []
        self.height = 0

    def add(self, element: str, bottom: float = 0) -> None:
        self.parts.append(element)
        self.height = max(self.height, bottom)

    def text(self, x, y, s, *, size=13, fill=INK, weight="normal", anchor="start", font=SANS,
             italic=False):
        style = ' font-style="italic"' if italic else ""
        if font == MONO:
            style += ' xml:space="preserve"'
        self.add(f'<text x="{x:.1f}" y="{y:.1f}" font-family="{font}" font-size="{size}" '
                 f'fill="{fill}" font-weight="{weight}" text-anchor="{anchor}"{style}>'
                 f'{escape(str(s))}</text>', y + 6)

    def rect(self, x, y, w, h, *, fill, stroke="none", rx=4, width=1.0):
        self.add(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{rx}" '
                 f'fill="{fill}" stroke="{stroke}" stroke-width="{width}"/>', y + h)

    def line(self, x1, y1, x2, y2, *, stroke=LINE, width=1.2, dash=None, arrow=False):
        d = f' stroke-dasharray="{dash}"' if dash else ""
        m = ' marker-end="url(#arrow)"' if arrow else ""
        self.add(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
                 f'stroke="{stroke}" stroke-width="{width}"{d}{m}/>', max(y1, y2))

    def save(self, name: str) -> Path:
        height = int(self.height + 28)
        body = "\n  ".join(self.parts)
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.width}" height="{height}" '
               f'viewBox="0 0 {self.width} {height}">\n'
               f'  <title>{escape(self.title)}</title>\n'
               f'  <defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
               f'markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" '
               f'fill="#6b7280"/></marker></defs>\n'
               f'  <rect width="{self.width}" height="{height}" rx="14" fill="#ffffff"/>\n'
               f'  {body}\n</svg>\n')
        path = OUT / f"{name}.svg"
        path.write_text(svg)
        return path


# ---------------------------------------------------------------- hex rows

CELL, GAP, ROW_H = 30, 3, 26


@dataclass
class Span:
    data: bytes
    kind: str
    label: str = ""
    note: str = ""


def hex_row(svg: Svg, x: float, y: float, spans: list[Span], *, caption="", offset=None) -> float:
    """One labelled row of byte cells; returns the y below its annotations."""
    if caption:
        svg.text(x, y + 17, caption, size=12, fill=MUTED, weight="600")
    cx = x + 130
    if offset is not None:
        svg.text(cx - 12, y + 17, f"{offset:04x}", size=11.5, fill="#9ca3af", anchor="end", font=MONO)
    bottom = y + ROW_H
    for span in spans:
        k = FIELDS[span.kind]
        start = cx
        for b in span.data:
            svg.rect(cx, y, CELL, ROW_H, fill=k.bg, rx=4)
            svg.text(cx + CELL / 2, y + 17.5, f"{b:02x}", size=13, fill=k.fg, anchor="middle",
                     font=MONO, weight="600")
            cx += CELL + GAP
        end = cx - GAP
        if span.label or span.note:
            mid = (start + end) / 2
            svg.line(start + 2, y + ROW_H + 5, end - 2, y + ROW_H + 5, stroke=k.fg, width=1.5)
            if span.label:
                svg.text(mid, y + ROW_H + 21, span.label, size=11.5, fill=k.fg, anchor="middle",
                         weight="600")
                bottom = max(bottom, y + ROW_H + 21)
            if span.note:
                svg.text(mid, y + ROW_H + 36, span.note, size=11, fill=MUTED, anchor="middle")
                bottom = max(bottom, y + ROW_H + 36)
        cx += 10
    return bottom + 18


def heading(svg: Svg, title: str, subtitle: str = "") -> float:
    svg.text(30, 40, title, size=20, weight="700")
    if subtitle:
        svg.text(30, 63, subtitle, size=13, fill=MUTED)
    return 86


def legend(svg: Svg, x: float, y: float, kinds: list[str]) -> float:
    cx = x
    for name in kinds:
        k = FIELDS[name]
        width = 22 + len(k.name) * 6.6
        if cx + width > svg.width - 30:
            cx, y = x, y + 22
        svg.rect(cx, y, 14, 14, fill=k.bg, stroke=k.fg, rx=3)
        svg.text(cx + 20, y + 11.5, k.name, size=11.5, fill=INK)
        cx += width + 14
    return y + 30


def u32(value: int) -> bytes:
    return struct.pack("<I", value)


# ---------------------------------------------------------------- figures

def fig_frame() -> Path:
    frame = encode_frame(u32(1) + u32(0x14))          # HELLO request, txn 1
    svg = Svg(900, "Frame anatomy: the HELLO request")
    y = heading(svg, "Frame anatomy", "Every message in both directions is one frame: an 8-byte "
                "header and a payload. All integers are little-endian.")
    # schematic bar: the header split into its fields, then the payload
    parts = (("magic", "u32", 150, "magic"), ("length", "u16", 90, "length"),
             ("checksum", "u16", 90, "checksum"))
    cx = 30
    for kind, typ, w, label in parts:
        k = FIELDS[kind]
        svg.rect(cx, y, w, 34, fill=k.bg, stroke=k.fg, rx=6)
        svg.text(cx + w / 2, y + 22, label, size=13, anchor="middle", weight="600", fill=k.fg)
        svg.text(cx + w / 2, y + 52, typ, size=11.5, fill=MUTED, anchor="middle", font=MONO)
        cx += w + 4
    svg.rect(cx, y, 870 - cx, 34, fill="#f9fafb", stroke="#cbd5e1", rx=6)
    svg.text((cx + 870) / 2, y + 22, "payload · length bytes", size=13, anchor="middle",
             weight="600")
    svg.text((cx + 870) / 2, y + 52, "at most the max payload from HELLO (32 800 on the Ace)",
             size=11.5, fill=MUTED, anchor="middle")
    svg.text(30 + 165, y + 70, "header · 8 bytes", size=11.5, fill=MUTED, anchor="middle")
    y += 18
    y += 78
    svg.text(30, y, "Example: HELLO request as sent by the host (transaction 1)", size=13.5,
             weight="600")
    y += 14
    y = hex_row(svg, 30, y, [
        Span(frame[0:4], "magic", "magic", "0x7fb52ca3"),
        Span(frame[4:6], "length", "length", f"{len(frame) - 8} bytes"),
        Span(frame[6:8], "checksum", "checksum", f"0x{checksum(frame[8:]):04x}"),
        Span(frame[8:12], "txn", "txn", "1"),
        Span(frame[12:16], "cmd", "command", "0x14 HELLO"),
    ], caption="frame", offset=0)
    svg.text(30, y + 4, "magic, length and checksum form the header; transaction id and command "
             "are the request payload (here without arguments).", size=12.5, fill=MUTED)
    y += 30
    svg.text(30, y, "Reply from the device (payload only)", size=13.5, weight="600")
    y += 14
    p = CAP_HELLO_RESP
    y = hex_row(svg, 30, y, [
        Span(p[0:4], "type", "packet type", "0xff99030e response"),
        Span(p[4:8], "txn", "txn", "1"),
        Span(p[8:12], "status", "error code", "0 = ok"),
        Span(p[12:16], "data", "protocol", "3"),
    ], caption="payload", offset=8)
    y = hex_row(svg, 30, y, [
        Span(p[16:20], "data", "max payload", f"{struct.unpack_from('<I', p, 16)[0]} bytes"),
        Span(p[20:24], "data", "device type", '"Ace " (reversed)'),
        Span(p[24:28], "data", "feature bits", f"0x{struct.unpack_from('<I', p, 24)[0]:02x}"),
    ], offset=24)
    legend(svg, 30, y, ["magic", "length", "checksum", "type", "txn", "cmd", "status", "data"])
    return svg.save("protocol-frame")


def fig_checksum() -> Path:
    frame = CAP_GET_FW
    payload = frame[8:]
    total = checksum(payload)
    svg = Svg(900, "How the checksum is computed")
    y = heading(svg, "Checksum: a 16-bit sum, not a CRC",
                "The checksum is the sum of all payload bytes, truncated to 16 bits. "
                "The 8 header bytes are not included.")
    svg.text(30, y, "Example: GET_PROPERTY fwversion (0x81), transaction 2, as captured", size=13.5,
             weight="600")
    y += 14
    y = hex_row(svg, 30, y, [
        Span(frame[0:4], "magic", "magic"), Span(frame[4:6], "length", "length", "16"),
        Span(frame[6:8], "checksum", "checksum", f"0x{total:04x}"),
    ], caption="header", offset=0)
    y = hex_row(svg, 30, y, [
        Span(payload[0:4], "txn", "txn 2"), Span(payload[4:8], "cmd", "0x64 GET"),
        Span(payload[8:12], "arg", "id 0x81"), Span(payload[12:16], "arg", "0"),
    ], caption="payload", offset=8)
    # the sum
    svg.text(30, y + 6, "Sum of the payload bytes:", size=13.5, weight="600")
    y += 22
    terms = [b for b in payload]
    cx = 30
    for i, b in enumerate(terms):
        kind = "txn" if i < 4 else "cmd" if i < 8 else "arg"
        k = FIELDS[kind]
        svg.rect(cx, y, 30, 24, fill=k.bg, rx=4)
        svg.text(cx + 15, y + 16.5, f"{b:02x}", size=12.5, fill=k.fg, anchor="middle", font=MONO,
                 weight="600")
        cx += 30
        svg.text(cx + 6, y + 16.5, "+" if i < len(terms) - 1 else "=", size=13, fill=MUTED,
                 anchor="middle")
        cx += 13
    svg.rect(cx + 2, y, 66, 24, fill=FIELDS["checksum"].bg, rx=4)
    svg.text(cx + 35, y + 16.5, f"0x{total:04x}", size=12.5, fill=FIELDS["checksum"].fg,
             anchor="middle", font=MONO, weight="700")
    y += 48
    svg.text(30, y, f"0x02 + 0x64 + 0x81 = 0x{total:04x} (all other bytes are zero). "
             f"Stored little-endian in header bytes 6–7:  {total & 0xff:02x} {total >> 8:02x}",
             size=13, font=MONO, fill=INK)
    y += 34
    big = bytes([0xFF]) * 300
    svg.text(30, y, f"Only the low 16 bits are kept: 300 bytes of 0xff add up to 0x{sum(big):05x}, "
             f"so the checksum field is 0x{checksum(big):04x}.", size=13, fill=INK)
    y += 34
    code = ["def checksum(payload: bytes) -> int:", "    return sum(payload) & 0xFFFF",
            "", "frame = struct.pack('<IHH', 0x7FB52CA3, len(payload), checksum(payload)) + payload"]
    svg.rect(30, y, 840, 22 * len(code) + 16, fill="#0f172a", rx=8)
    for i, line in enumerate(code):
        svg.text(48, y + 26 + 22 * i, line, size=13, fill="#e2e8f0", font=MONO)
    y += 22 * len(code) + 40
    svg.text(30, y, "Receivers check the sum and drop frames that do not match; they then search "
             "for the next magic word (resynchronisation).", size=12.5, fill=MUTED)
    return svg.save("protocol-checksum")


def fig_sequence() -> Path:
    svg = Svg(900, "A measurement session, as captured")
    y = heading(svg, "A measurement session", "Requests (→) are answered by responses with the "
                "same transaction id; data and updates (←) arrive unsolicited.")
    hx, dx = 210, 690
    top = y + 10
    svg.rect(hx - 70, top, 140, 34, fill="#ecfeff", stroke="#22d3ee", rx=8)
    svg.text(hx, top + 22, "host (otii-usb)", size=13.5, weight="600", anchor="middle")
    svg.rect(dx - 70, top, 140, 34, fill="#eef2ff", stroke="#818cf8", rx=8)
    svg.text(dx, top + 22, "Otii Ace Pro", size=13.5, weight="600", anchor="middle")
    steps = [
        ("→", "HELLO  txn 1", "cmd"), ("←", "protocol 3 · max payload 32800 · \"Ace \" · 0x3f", "type"),
        ("→", "GET_PROPERTY fwversion (0x81)  txn 2", "cmd"), ("←", "\"3.2.5\"", "type"),
        ("→", "GET_PROPERTY chinfo (0x8d)  txn 4", "cmd"), ("←", "18 channel records × 16 bytes", "type"),
        ("→", "SET_CHANNEL mv / mc / mp on  txn 5–7", "cmd"), ("←", "ok × 3", "type"),
        ("→", "RECORD_START  txn 8", "cmd"), ("←", "ok", "type"),
        ("⇐", "DATA  seq 0, 1, 2, …  one packet per millisecond", "seq"),
        ("→", "PING (keepalive, every 0.5 s while streaming)", "cmd"), ("⇐", "PROPERTY_UPDATE (live values)", "data"),
        ("→", "RECORD_STOP  txn 9", "cmd"), ("←", "ok", "type"),
        ("→", "SET_CHANNEL mp / mc / mv off  txn 10–12", "cmd"), ("←", "ok × 3", "type"),
    ]
    y = top + 50
    lane_top = y - 6
    for direction, label, kind in steps:
        k = FIELDS[kind]
        to_device = direction == "→"
        x1, x2 = (hx, dx) if to_device else (dx, hx)
        dash = "5 4" if direction == "⇐" else None
        svg.line(x1, y + 8, x2 + (-8 if to_device else 8), y + 8, stroke=k.fg, width=1.6, dash=dash,
                 arrow=True)
        svg.text((hx + dx) / 2, y + 2, label, size=12.5, fill=k.fg, anchor="middle", weight="600")
        y += 34
    svg.line(hx, lane_top, hx, y, stroke=LINE, dash="3 4")
    svg.line(dx, lane_top, dx, y, stroke=LINE, dash="3 4")
    svg.text(30, y + 14, "Solid: request/response. Dashed: unsolicited packets from the device.",
             size=12, fill=MUTED)
    return svg.save("protocol-sequence")


def fig_data_packet() -> Path:
    payload = CAP_DATA
    frame = encode_frame(payload)
    svg = Svg(1000, "Anatomy of a data packet, as captured")
    y = heading(svg, "Data packet (one per millisecond)",
                f"Captured from an Otii Ace Pro with channels mc, mv, mp enabled at 50 kS/s: "
                f"{len(frame)} bytes on the wire ({len(payload)} payload).")
    names = {0: "mc · current [A]", 1: "mv · voltage [V]", 6: "mp · power [W]"}
    units = {0: ("A", 1e-9, "nA"), 1: ("V", 1e-3, "mV"), 6: ("W", 1e-9, "nW")}
    y = hex_row(svg, 30, y, [
        Span(frame[0:4], "magic", "magic"),
        Span(frame[4:6], "length", "length", f"{len(payload)}"),
        Span(frame[6:8], "checksum", "checksum", f"0x{checksum(payload):04x}"),
    ], caption="frame header", offset=0)
    y = hex_row(svg, 30, y, [
        Span(payload[0:4], "type", "packet type", "0xff2a8369 DATA"),
        Span(payload[4:8], "seq", "sequence", f"{struct.unpack_from('<I', payload, 4)[0]} (ms since start)"),
    ], caption="packet header", offset=8)
    off = 8
    block_no = 1
    while off < len(payload):
        ch, n, sps = struct.unpack_from("<HHI", payload, off)
        y = hex_row(svg, 30, y, [
            Span(payload[off:off + 2], "channel", "channel", f"{ch} = {names.get(ch, ch).split(' ·')[0]}"),
            Span(payload[off + 2:off + 4], "count", "count", f"{n} samples"),
            Span(payload[off + 4:off + 8], "rate", "sample rate", f"{sps:,} S/s".replace(",", " ")),
        ], caption=f"block {block_no} header", offset=8 + off)
        values = struct.unpack_from(f"<{n}f", payload, off + 8)
        _, scale, unit = units.get(ch, ("", 1.0, ""))
        spans = []
        for i in range(4):
            raw = payload[off + 8 + 4 * i: off + 12 + 4 * i]
            spans.append(Span(raw, "sample" if i % 2 == 0 else "sample2", f"f32 #{i}",
                              f"{values[i] / scale:.1f} {unit}"))
        short = names.get(ch, str(ch)).split(" ·")[0]
        y = hex_row(svg, 30, y, spans, caption=f"{short} samples", offset=8 + off + 8)
        svg.text(160, y - 2, f"⋯ {n - 4} more samples ({4 * (n - 4)} bytes) · "
                 f"mean of all {n}: {sum(values) / n / scale:.2f} {unit}", size=12, fill=MUTED,
                 italic=True)
        y += 26
        off += 8 + 4 * n
        block_no += 1
    svg.text(30, y + 4, "A block is [u16 channel][u16 count][u32 sample rate] followed by count "
             "samples of 4 bytes. Float32 samples are already in SI units (channel info flag bit 15).",
             size=12.5, fill=MUTED)
    legend(svg, 30, y + 22, ["magic", "length", "checksum", "type", "seq", "channel", "count",
                             "rate", "sample"])
    return svg.save("protocol-data-packet")


def fig_property_id() -> Path:
    svg = Svg(900, "Property ids and property requests")
    y = heading(svg, "Property ids", "A property id is a base id (bits 0–9) plus an attribute "
                "selector (bits 10–13) that picks the value, a limit, or the float form.")
    # bit field bar
    bits = [("31 … 14", "reserved (0)", "unknown", 300), ("13 … 10", "attribute", "flags", 200),
            ("9 … 0", "base id", "arg", 300)]
    cx = 50
    for rng, label, kind, w in bits:
        k = FIELDS[kind]
        svg.rect(cx, y, w, 40, fill=k.bg, stroke=k.fg, rx=6)
        svg.text(cx + w / 2, y + 25, label, size=13.5, fill=k.fg, anchor="middle", weight="600")
        svg.text(cx + w / 2, y + 58, f"bits {rng}", size=11.5, fill=MUTED, anchor="middle", font=MONO)
        cx += w + 4
    y += 86
    rows = [("0x0000", "value", "the property itself"), ("0x0400", "min", "lower limit"),
            ("0x0800", "max", "upper limit"), ("0x0c00", "enum", "list of allowed values"),
            ("0x1000", "float", "value as float32"), ("0x1400", "step", "step size")]
    svg.text(50, y, "attribute", size=12.5, fill=MUTED, weight="600")
    svg.text(160, y, "selects", size=12.5, fill=MUTED, weight="600")
    examples = [("0x000b", "main voltage setpoint (i32 µV)"), ("0x080b", "its maximum: 25 000 000 µV"),
                ("0x100c", "current limit as f32 amperes"), ("0x0c0a", "power regulation enum")]
    svg.text(520, y, "examples", size=12.5, fill=MUTED, weight="600")
    for i, (code, name, text) in enumerate(rows):
        yy = y + 22 + 21 * i
        svg.text(50, yy, code, size=13, font=MONO, fill=FIELDS["flags"].fg, weight="600")
        svg.text(160, yy, f"{name} — {text}", size=13)
    for i, (code, text) in enumerate(examples):
        yy = y + 22 + 21 * i
        svg.text(520, yy, code, size=13, font=MONO, fill=FIELDS["arg"].fg, weight="600")
        svg.text(590, yy, text, size=13)
    y += 22 + 21 * len(rows) + 20
    svg.text(30, y, "GET_PROPERTY main voltage setpoint (0x0b), transaction 5", size=13.5, weight="600")
    y += 14
    req = u32(5) + u32(0x64) + u32(0x0b) + u32(0)
    y = hex_row(svg, 30, y, [Span(req[0:4], "txn", "txn"), Span(req[4:8], "cmd", "0x64 GET"),
                             Span(req[8:12], "arg", "id 0x0b"), Span(req[12:16], "arg", "always 0")],
                caption="request", offset=8)
    resp = struct.pack("<IIIi", 0xFF99030E, 5, 0, 3_000_000)
    y = hex_row(svg, 30, y, [Span(resp[0:4], "type", "response"), Span(resp[4:8], "txn", "txn"),
                             Span(resp[8:12], "status", "ok"),
                             Span(resp[12:16], "data", "value", "3 000 000 µV = 3.0 V")],
                caption="response", offset=8)
    svg.text(30, y, "SET_PROPERTY (0x66) carries [u32 id][4-byte value]; an id the device does not "
             "know is answered with error code 0xfffffffd.", size=12.5, fill=MUTED)
    return svg.save("protocol-property-id")


def fig_chinfo() -> Path:
    svg = Svg(900, "Channel info records")
    y = heading(svg, "Channel info (property 0x8d)", "One 16-byte record per channel; the "
                "Ace Pro reports 18. Decoding samples depends on the flags.")
    r = CAP_CHINFO_MC
    flags = struct.unpack_from("<H", r, 2)[0]
    y = hex_row(svg, 30, y, [
        Span(r[0:2], "channel", "channel", "0 = mc"),
        Span(r[2:4], "flags", "flags", f"0x{flags:04x}"),
        Span(r[4:5], "unknown", "aux", "50"),
        Span(r[5:8], "unknown", "padding"),
        Span(r[8:12], "rate", "sample rate", "50 000 S/s"),
        Span(r[12:16], "data", "scale", "f32 0.0 (unused)"),
    ], caption="analog channel", offset=0)
    g = CAP_CHINFO_GPI
    y = hex_row(svg, 30, y, [
        Span(g[0:2], "channel", "channel", "0x16 = gpi"),
        Span(g[2:4], "flags", "flags", "kind 2 digital"),
        Span(g[4:16], "unknown", "no rate, no scale"),
    ], caption="digital channel", offset=0)
    y += 4
    svg.text(30, y, "flags", size=13.5, weight="600")
    rows = [("bit 15", "samples are float32 (otherwise int32)"),
            ("bit 14", "multiply samples by scale"),
            ("bits 0–7", "kind: 0 analog · 1 UART · 2 digital")]
    for i, (b, text) in enumerate(rows):
        svg.text(50, y + 24 + 21 * i, b, size=13, font=MONO, fill=FIELDS["flags"].fg, weight="600")
        svg.text(150, y + 24 + 21 * i, text, size=13)
    y += 24 + 21 * len(rows) + 14
    svg.text(30, y, "0x8000 = float32, not scaled, analog: the samples are SI values as they are.",
             size=12.5, fill=MUTED)
    return svg.save("protocol-chinfo")


def fig_update() -> Path:
    u = CAP_UPDATE
    svg = Svg(900, "Property update packet (observed)")
    y = heading(svg, "Property update (observed, not used by the driver)",
                "Unsolicited packets of type 0xff2ff2ce; most are empty, some carry a list of records.")
    y = hex_row(svg, 30, y, [Span(u[0:4], "type", "packet type", "0xff2ff2ce"),
                             Span(u[4:8], "unknown", "0")], caption="header", offset=0)
    y = hex_row(svg, 30, y, [Span(u[8:10], "flags", "type 2"), Span(u[10:12], "length", "len 12"),
                             Span(u[12:24], "unknown", "meaning unknown")], caption="record", offset=8)
    names = {0x01: ("mv", 1e-3, "mV"), 0x00: ("mc", 1e-9, "nA"), 0x06: ("mp", 1e-9, "nW"),
             0x10: ("vb", 1.0, "V"), 0x11: ("vj", 1.0, "V")}
    off = 24
    for _ in range(5):
        ch, value = struct.unpack_from("<If", u, off + 4)
        name, scale, unit = names[ch]
        y = hex_row(svg, 30, y, [
            Span(u[off:off + 2], "flags", "type 2"), Span(u[off + 2:off + 4], "length", "len 8"),
            Span(u[off + 4:off + 8], "channel", "channel", f"{ch:#04x} {name}"),
            Span(u[off + 8:off + 12], "sample", "f32 value", f"{value / scale:.2f} {unit}"),
        ], caption="record", offset=off)
        off += 12
    svg.text(30, y, "Records are [u16 type][u16 length][body]. Type-2 records of length 8 hold "
             "[u32 channel][f32 value]; mv/mc/mp match the", size=12.5, fill=MUTED)
    svg.text(30, y + 18, "concurrent measurement. vb (≈ 5 V) and vj (≈ 19 V) look like the USB and "
             "DC-jack supply monitors. The packet goes on with further records.", size=12.5, fill=MUTED)
    return svg.save("protocol-update")


def render_pngs(paths: list[Path]) -> None:
    tool = shutil.which("rsvg-convert")
    if tool is None:
        print("rsvg-convert not found: PNGs not rendered")
        return
    target = OUT / "png"
    target.mkdir(exist_ok=True)
    for svg in paths:
        subprocess.run([tool, "-z", "2", "-o", str(target / f"{svg.stem}.png"), str(svg)], check=True)


def main() -> None:
    paths = [fig_frame(), fig_checksum(), fig_sequence(), fig_data_packet(), fig_property_id(),
             fig_chinfo(), fig_update()]
    render_pngs(paths)
    for p in paths:
        print(p.relative_to(OUT.parents[1]))


if __name__ == "__main__":
    main()
