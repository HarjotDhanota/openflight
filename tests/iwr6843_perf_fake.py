"""The IWR6843 firmware fake with the perfstats and l3bench replies (research tests)."""

from tests.iwr6843_firmware_fake import FirmwareModel

# A made-up perfstats reply in the firmware's format (l3_cli_perfStats), for parser tests only.
PERF_REPLY = (
    b"perf tick_hz=200000000 rearm_max=41000 rearms=900 task_max=90000 task_runs=900 "
    b"margin_min=52000 margin_frames=1200\r\n"
    b"perf scan_max=38000 scan_frames=900 scan_total_hi=1 scan_total_lo=5 "
    b"pack_max=12000 packs=900 pack_overlap=0 iq8_overflow=0 iq8_overflow_saturated=0\r\n"
    b"heap size=32768 free=9000 free_min=8800 largest=8000\r\n"
    b"stacks init=2900/8192 ctrl=700/3072 rearm=600/2048 cli=1500/4096 hwi=900/2048\r\n"
)
BENCH_REPLY = b"bench samples=183168 cycles=4200000 sum_hi=1 sum_lo=5\r\n"


class PerfFirmwareModel(FirmwareModel):
    """The board, also answering perfstats and l3bench."""

    def reply(self, line: str) -> bool:
        if line == "perfstats":
            self.pending += PERF_REPLY
        elif line == "l3bench":
            if self.active:
                self.pending += b"Error: stop the sensor before l3bench\r\nError -1\r\nl3dump:/>"
                return False
            self.pending += BENCH_REPLY
        return True
