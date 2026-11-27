# test_protocol.py: framing, packets and the session
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
# Frame round trips, byte-by-byte feeding, resynchronisation after garbage and
# checksum errors, packet encoders/parsers, request/response, error codes,
# timeouts, the text-mode fallback and keepalives (REQ-PROT-LINK-001,
# REQ-PROT-LINK-002).

import struct

import pytest

from otii_usb.errors import CommandError, ProtocolError, ResponseTimeoutError
from otii_usb.protocol import Command, DataPacket, FrameDecoder, Response, SampleBlock, Session
from otii_usb.protocol.framing import checksum, encode_frame
from otii_usb.protocol.packets import (Request, encode_data_packet, encode_request,
                                       encode_response, parse_packet, parse_request)
from tests.conftest import SilentTransport


class TestFraming:
    def test_roundtrip(self):
        payload = bytes(range(40))
        assert FrameDecoder().feed(encode_frame(payload)) == [payload]

    def test_header_layout(self):
        frame = encode_frame(b"\x01\x02")
        assert frame[:4] == bytes.fromhex("a32cb57f")
        assert struct.unpack_from("<HH", frame, 4) == (2, 3)

    def test_byte_by_byte_feed(self):
        frames = encode_frame(b"abc") + encode_frame(b"defg")
        decoder, out = FrameDecoder(), []
        for b in frames:
            out += decoder.feed(bytes([b]))
        assert out == [b"abc", b"defg"]

    def test_skips_text_and_garbage(self):
        discarded = []
        decoder = FrameDecoder(on_discard=discarded.append)
        assert decoder.feed(b"init:ok\r\n" + encode_frame(b"xy")) == [b"xy"]
        assert b"".join(discarded) == b"init:ok\r\n"

    def test_resyncs_after_bad_checksum(self):
        bad = bytearray(encode_frame(b"zz"))
        bad[6] ^= 0xFF
        decoder = FrameDecoder()
        assert decoder.feed(bytes(bad) + encode_frame(b"ok")) == [b"ok"]
        assert decoder.checksum_errors == 1

    def test_checksum_wraps(self):
        assert checksum(b"\xff" * 300) == (255 * 300) & 0xFFFF


class TestPackets:
    def test_request_roundtrip(self):
        req = Request(7, Command.GET_PROPERTY, b"\x09\0\0\0\0\0\0\0")
        assert parse_request(encode_request(req)) == req

    def test_response_roundtrip(self):
        resp = Response(5, 0, b"Ace")
        assert parse_packet(encode_response(resp)) == resp

    def test_data_roundtrip(self):
        pkt = DataPacket(3, (SampleBlock(0, 2, 50_000, bytes(8)), SampleBlock(6, 1, 1000, bytes(4))))
        assert parse_packet(encode_data_packet(pkt)) == pkt

    def test_truncated_data_block(self):
        raw = encode_data_packet(DataPacket(0, (SampleBlock(0, 2, 1, bytes(8)),)))
        with pytest.raises(ProtocolError):
            parse_packet(raw[:-1])


class TestSession:
    def test_handshake(self, simulator):
        with Session(simulator) as session:
            hello = session.handshake()
        assert (hello.protocol_version, hello.max_payload) == (3, 32800)

    def test_command_error(self, simulator):
        session = Session(simulator)
        session.handshake()
        with pytest.raises(CommandError) as exc:
            session.request(0x55)
        assert exc.value.code == 1

    def test_timeout(self):
        with pytest.raises(ResponseTimeoutError):
            Session(SilentTransport()).request(Command.PING, timeout=0.05)

    def test_handshake_gives_up_and_sends_text_init(self):
        transport = SilentTransport()
        with pytest.raises(ProtocolError):
            Session(transport).handshake(attempts=2, timeout=0.02)
        assert b"\rinit\r\n" in transport.written

    def test_keepalive_during_pump(self, simulator):
        session = Session(simulator, keepalive_interval=0.02)
        session.handshake()
        session.pump(0.1)
        assert sum(r.command == Command.PING for r in simulator.requests) >= 2
