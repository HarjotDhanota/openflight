"""Research-only ROM SRAM download for a temporary IWR clock experiment.

SWRA627 uses storage 4 and WRITE FILE TO RAM (0x26). This module sends no flash
erase/write command. Running an image is still a hardware experiment, not a
claim that its acquisition timing or firmware behavior has been qualified.
"""

from __future__ import annotations

import struct
import zlib

from openflight.iwr6843.bootloader import (
    FILE_TYPE_META_IMAGE1,
    MAX_CHUNK_SIZE,
    OP_CLOSE,
    OP_OPEN,
    IWR6843Bootloader,
)

STORAGE_SRAM = 4
OP_WRITE_RAM = 0x26
DEVICE_ID_XWR68XX = 0x37


def validate_ram_image(image):
    """Check the SDK meta-header length/device; CRC64 stays for ROM verification."""
    if not isinstance(image, bytes) or len(image) < 128 or image[:4] != b"MSTR":
        raise ValueError("RAM download requires an SDK MSTR meta-image")
    _, count, device, _, _, size = struct.unpack_from("<6I", image)
    if not 1 <= count <= 4 or device != DEVICE_ID_XWR68XX or size != len(image):
        raise ValueError("meta-image device, file count or length mismatch")
    if 24 + count * 32 > len(image):
        raise ValueError("meta-image component header is truncated")
    return image


def ram_image_from_flash(image):
    """Remove only a verified little-endian flash CRC32, as SWRA627 requires."""
    if not isinstance(image, bytes) or len(image) < 132:
        raise ValueError("flash meta-image is missing its CRC32")
    body, footer = image[:-4], image[-4:]
    if zlib.crc32(body) != struct.unpack("<I", footer)[0]:
        raise ValueError("flash meta-image CRC32 mismatch")
    return validate_ram_image(body)


class IWR6843RamClockLoader(IWR6843Bootloader):
    """A RAM-only transfer; reset into functional mode returns to saved firmware."""

    def load_ram(self, image, *, handshake=True, progress=None):
        """Return after CLOSE acknowledgement, before declaring application health.

        The RAM application can start after CLOSE, so no subsequent bootloader
        GET STATUS is used as a fabricated application-start acknowledgement.
        """
        validate_ram_image(image)
        if handshake:
            self.handshake()
        self._send(
            OP_OPEN, struct.pack(">IIII", len(image), STORAGE_SRAM, FILE_TYPE_META_IMAGE1, 0)
        )
        for offset in range(0, len(image), MAX_CHUNK_SIZE):
            chunk = image[offset : offset + MAX_CHUNK_SIZE]
            self._send(OP_WRITE_RAM, chunk)
            if progress is not None:
                progress(offset + len(chunk), len(image))
        self._send(OP_CLOSE, struct.pack(">I", STORAGE_SRAM))
