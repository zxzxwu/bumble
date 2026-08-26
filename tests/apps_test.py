# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Tests for Bumble apps."""

import asyncio
import datetime
import json
import pathlib
import urllib.request
from typing import Any

import click.testing
import pytest
import websockets.asyncio.client

from apps import (
    bench,
    ble_rpa_tool,
    console,
    controller_info,
    controller_loopback,
    gatt_dump,
    l2cap_bridge,
    pair,
    rfcomm_bridge,
    scan,
    show,
    unbond,
    usb_probe,
)
from apps.player import player
from apps.speaker import speaker
from tools import (
    intel_fw_download,
    intel_util,
    rtk_fw_download,
    rtk_util,
)

lea_unicast_app: Any
try:
    from apps.lea_unicast import app as lea_unicast_app
except ImportError:
    lea_unicast_app = None

auracast: Any
try:
    from apps import auracast
except ImportError:
    auracast = None

APPS = [
    bench.bench,
    ble_rpa_tool.main,
    console.main,
    controller_info.main,
    controller_loopback.main,
    gatt_dump.main,
    l2cap_bridge.cli,
    rfcomm_bridge.cli,
    pair.main,
    scan.main,
    show.main,
    unbond.main,
    usb_probe.main,
    player.player_cli,
    speaker.speaker,
    intel_util.main,
    getattr(intel_fw_download, "main"),
    rtk_util.main,
    getattr(rtk_fw_download, "main"),
]

if auracast is not None:
    APPS.append(auracast.auracast)


@pytest.mark.parametrize(
    "app_main", APPS, ids=lambda cmd: getattr(cmd, "name", repr(cmd))
)
def test_app_help(app_main: Any) -> None:
    runner = click.testing.CliRunner()
    result = runner.invoke(app_main, ["--help"])
    assert result.exit_code == 0
    assert "Usage:" in result.output


def test_ble_rpa_tool() -> None:
    runner = click.testing.CliRunner()
    main_cmd: Any = ble_rpa_tool.main

    # Test gen-irk
    irk_result = runner.invoke(main_cmd, ["gen-irk"])
    assert irk_result.exit_code == 0
    irk = irk_result.output.strip()
    assert len(irk) == 32

    # Test gen-rpa
    gen_result = runner.invoke(main_cmd, ["gen-rpa", irk])
    assert gen_result.exit_code == 0
    rpa = gen_result.output.strip()
    assert len(rpa.split(":")) == 6

    # Test verify-rpa (match)
    verify_result = runner.invoke(main_cmd, ["verify-rpa", irk, rpa])
    assert verify_result.exit_code == 0
    assert "Verified" in verify_result.output

    # Test verify-rpa (mismatch)
    wrong_irk = "ffeeddccbbaa99887766554433221100"
    verify_mismatch = runner.invoke(main_cmd, ["verify-rpa", wrong_irk, rpa])
    assert verify_mismatch.exit_code == 0
    assert "Not Verified" in verify_mismatch.output


def test_show_tool() -> None:
    runner = click.testing.CliRunner()
    main_cmd: Any = show.main
    hci_data_file = pathlib.Path(__file__).parent / "hci_data_001.bin"
    result = runner.invoke(main_cmd, ["--format", "h4", str(hci_data_file)])
    assert result.exit_code == 0


def test_unbond(tmp_path: pathlib.Path) -> None:
    runner = click.testing.CliRunner()
    main_cmd: Any = unbond.main
    keystore_file = tmp_path / "keystore.json"
    keystore_file.write_text("{}", encoding="utf-8")
    result = runner.invoke(main_cmd, ["--keystore-file", str(keystore_file)])
    assert result.exit_code == 0


def test_console_helpers() -> None:
    # Test natural_time
    now = datetime.datetime.now()
    assert console.natural_time(now) == "now"
    assert (
        console.natural_time(now - datetime.timedelta(seconds=10)) == "10 seconds ago"
    )
    assert console.natural_time(now - datetime.timedelta(minutes=5)) == "5 minutes ago"
    assert console.natural_time(now - datetime.timedelta(hours=2)) == "2 hours ago"
    assert console.natural_time(now - datetime.timedelta(days=3)) == "3 days ago"

    # Test format_table
    headers = ["Name", "Value"]
    rows: list[list[Any]] = [["Key1", "Val1"], ["Key2", "Val2"]]
    table_str = console.format_table(headers, rows)
    assert "Name" in table_str
    assert "Key1" in table_str
    assert "Val2" in table_str
    assert table_str.startswith("+")
    assert table_str.endswith("+")


class MockSpeaker:
    def __init__(self) -> None:
        self.codec = "SBC"
        self.connection = None

        class StreamState:
            name = "IDLE"

        self.stream_state = StreamState()


@pytest.mark.asyncio
async def test_speaker_ui_server() -> None:
    mock_speaker = MockSpeaker()
    ui_server = speaker.UiServer(mock_speaker, port=0)  # type: ignore[arg-type]
    await ui_server.start_http()
    try:
        assert ui_server.port != 0

        # Test HTTP GET static files
        def fetch(path: str) -> tuple[int, str]:
            url = f"http://127.0.0.1:{ui_server.port}{path}"
            with urllib.request.urlopen(url) as response:
                return response.status, response.headers.get("Content-Type", "")

        status, ct = await asyncio.to_thread(fetch, "/")
        assert status == 200
        assert "text/html" in ct

        status, ct = await asyncio.to_thread(fetch, "/speaker.js")
        assert status == 200
        assert "text/javascript" in ct

        status, ct = await asyncio.to_thread(fetch, "/speaker.css")
        assert status == 200
        assert "text/css" in ct

        status, ct = await asyncio.to_thread(fetch, "/logo.svg")
        assert status == 200
        assert "image/svg+xml" in ct

        # Test WebSocket
        ws_url = f"ws://127.0.0.1:{ui_server.port}/channel"
        async with websockets.asyncio.client.connect(ws_url) as ws:
            await ws.send(json.dumps({"type": "hello", "params": {}}))
            raw_msg = await ws.recv()
            assert isinstance(raw_msg, str)
            msg = json.loads(raw_msg)
            assert msg["type"] == "hello"
            assert msg["params"]["codec"] == "SBC"

            # Test send audio bytes
            await ui_server.send_audio(b"audio-payload")
            audio_bytes = await ws.recv()
            assert audio_bytes == b"audio-payload"
    finally:
        await ui_server.close()


@pytest.mark.skipif(lea_unicast_app is None, reason="lc3 is not installed")
@pytest.mark.asyncio
async def test_lea_unicast_ui_server() -> None:
    assert lea_unicast_app is not None
    mock_speaker = MockSpeaker()
    ui_server = lea_unicast_app.UiServer(mock_speaker, port=0)  # type: ignore[arg-type]
    await ui_server.start_http()
    try:
        assert ui_server.port != 0

        # Test HTTP GET static files
        def fetch(path: str) -> tuple[int, str]:
            url = f"http://127.0.0.1:{ui_server.port}{path}"
            with urllib.request.urlopen(url) as response:
                return response.status, response.headers.get("Content-Type", "")

        status, ct = await asyncio.to_thread(fetch, "/")
        assert status == 200
        assert "text/html" in ct

        status, ct = await asyncio.to_thread(fetch, "/index.html")
        assert status == 200
        assert "text/html" in ct

        # Test WebSocket
        ws_url = f"ws://127.0.0.1:{ui_server.port}/channel"
        async with websockets.asyncio.client.connect(ws_url) as ws:
            await ws.send(json.dumps({"type": "hello", "params": {}}))
            raw_msg = await ws.recv()
            assert isinstance(raw_msg, str)
            msg = json.loads(raw_msg)
            assert msg["type"] == "hello"
            assert msg["params"]["codec"] == "SBC"

            # Test send audio bytes
            await ui_server.send_audio(b"audio-payload-lea")
            audio_bytes = await ws.recv()
            assert audio_bytes == b"audio-payload-lea"
    finally:
        await ui_server.close()
