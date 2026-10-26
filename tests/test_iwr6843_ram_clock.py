"""Synthetic ROM exchanges prove RAM storage/opcodes, not target execution."""

import hashlib
import importlib.util
import struct
import zlib
from pathlib import Path

import pytest

from openflight.iwr6843.bootloader import (
    ACK,
    OP_CLOSE,
    OP_OPEN,
    OP_PING,
    BootloaderError,
)
from openflight.iwr6843.ram_clock import (
    FILE_TYPE_META_IMAGE1,
    OP_WRITE_RAM,
    STORAGE_SRAM,
    IWR6843RamClockLoader,
    ram_image_from_flash,
)
from tests.test_iwr6843_bootloader import FakeSerial, _response

SCRIPT = Path(__file__).parents[1] / "scripts/hardware-test/load_iwr_clock_ram.py"
SPEC = importlib.util.spec_from_file_location("load_iwr_clock_ram", SCRIPT)
cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cli)


def ram_image():
    data = bytearray(b"\0" * 512)
    struct.pack_into("<6I", data, 0, int.from_bytes(b"MSTR", "little"), 2, 0x37, 0, 0, len(data))
    return bytes(data)


def flash_image():
    body = ram_image()
    return body + struct.pack("<I", zlib.crc32(body))


def test_remove_only_validated_flash_footer_without_changing_payload():
    image = flash_image()
    assert ram_image_from_flash(image) == ram_image()
    assert len(image) - len(ram_image_from_flash(image)) == 4
    assert image.endswith(struct.pack("<I", zlib.crc32(ram_image())))


@pytest.mark.parametrize("mode", ["crc", "truncated", "magic", "device", "count", "size"])
def test_invalid_flash_image_is_not_converted(mode):
    image = bytearray(flash_image())
    if mode == "crc":
        image[-1] ^= 1
    elif mode == "truncated":
        image = image[:80]
    else:
        if mode == "magic":
            image[:4] = b"BAD!"
        elif mode == "device":
            struct.pack_into("<I", image, 8, 0x25)
        elif mode == "count":
            struct.pack_into("<I", image, 4, 0)
        else:
            struct.pack_into("<I", image, 20, len(image))
        image[-4:] = struct.pack("<I", zlib.crc32(image[:-4]))
    with pytest.raises(ValueError):
        ram_image_from_flash(bytes(image))


def test_flash_image_cannot_accidentally_be_sent_as_ram_image():
    serial = FakeSerial([])
    with pytest.raises(ValueError, match="length"):
        IWR6843RamClockLoader(serial).load_ram(flash_image())
    assert serial.writes == [] and serial.break_states == []


def test_ram_protocol_never_sends_flash_erase_write_or_status_after_start():
    image = ram_image()
    chunks = (len(image) + 239) // 240
    serial = FakeSerial([_response(ACK)] * (chunks + 4))
    progress = []
    IWR6843RamClockLoader(serial).load_ram(
        image, progress=lambda done, total: progress.append((done, total))
    )
    opcodes = [value[4] for value in serial.writes]
    assert opcodes == [OP_PING, OP_OPEN, *([OP_WRITE_RAM] * chunks), OP_CLOSE]
    assert not set(opcodes) & {0x24, 0x28, 0x23}
    assert struct.unpack(">IIII", serial.writes[1][5:]) == (
        len(image),
        STORAGE_SRAM,
        FILE_TYPE_META_IMAGE1,
        0,
    )
    assert struct.unpack(">I", serial.writes[-1][5:]) == (STORAGE_SRAM,)
    assert b"".join(value[5:] for value in serial.writes[2:-1]) == image
    assert all(len(value[5:]) <= 240 for value in serial.writes[2:-1])
    assert serial.break_states == [True, False]
    assert progress[-1] == (len(image), len(image))


def test_ram_loader_can_use_an_already_completed_rom_handshake():
    image = ram_image()
    serial = FakeSerial([_response(ACK)] * 5)
    IWR6843RamClockLoader(serial).load_ram(image, handshake=False)
    assert serial.writes[0][4] == OP_OPEN
    assert serial.break_states == []


@pytest.mark.parametrize("stage", ["open", "write", "close"])
def test_ram_nack_stops_without_flash_fallback(stage):
    replies = [_response(ACK), _response(ACK)]
    extra = {"open": 0, "write": 1, "close": 4}[stage]
    replies += [_response(ACK)] * extra + [_response(b"\x00\x33")]
    serial = FakeSerial(replies)
    with pytest.raises(BootloaderError):
        IWR6843RamClockLoader(serial).load_ram(ram_image())
    assert not any(value[4] in (0x24, 0x28) for value in serial.writes)


def test_short_uart_write_is_not_retried_as_flash():
    serial = FakeSerial([])
    serial.write = lambda _: 1
    with pytest.raises(BootloaderError, match="short"):
        IWR6843RamClockLoader(serial).load_ram(ram_image(), handshake=False)


def _args(tmp_path):
    image = tmp_path / "temporary.ram.bin"
    image.write_bytes(ram_image())
    return [
        str(image),
        "--port",
        "synthetic-port",
        "--sha256",
        hashlib.sha256(ram_image()).hexdigest(),
    ]


def test_cli_hash_mismatch_and_initial_cancel_never_open_uart(tmp_path, monkeypatch):
    args = _args(tmp_path)
    monkeypatch.setattr(cli, "open_serial", lambda _: pytest.fail("UART must stay closed"))
    bad = [*args[:-1], "0" * 64]
    assert cli.main(bad) == 1
    monkeypatch.setattr("builtins.input", lambda _: "CANCEL")
    assert cli.main(args) == 2


def test_cli_retains_exclusive_owner_and_manual_reset_order(tmp_path, monkeypatch):
    events = []

    class Lock:
        def __init__(self, _port):
            pass

        def __enter__(self):
            events.append("lock")

        def __exit__(self, *_args):
            events.append("unlock")

    class Port:
        def __enter__(self):
            events.append("open")
            return self

        def __exit__(self, *_args):
            events.append("close")

    class Loader:
        def __init__(self, _serial):
            pass

        def load_ram(self, image):
            assert image == ram_image()
            events.append("RAM only")

    def answer(prompt):
        if "READY" in prompt:
            events.append("prepared")
            return "READY"
        events.append("reset confirmed")
        return "RAM"

    monkeypatch.setattr(cli, "IWR6843DeviceLock", Lock)
    monkeypatch.setattr(cli, "open_serial", lambda _: Port())
    monkeypatch.setattr(cli, "IWR6843RamClockLoader", Loader)
    monkeypatch.setattr("builtins.input", answer)
    assert cli.main(_args(tmp_path)) == 0
    assert events == ["prepared", "lock", "open", "reset confirmed", "RAM only", "close", "unlock"]
