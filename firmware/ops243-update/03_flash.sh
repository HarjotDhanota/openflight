#!/usr/bin/env bash
# Explicitly invoked flashing step. Keep stable power throughout.
set -euo pipefail
cd -- "$(dirname -- "$0")"
python3 prepare_update.py --check
echo "Writing OPS243-A v1.3.3 after live backup comparison. Keep power connected."
sudo openocd -f interface/cmsis-dap.cfg \
  -c "transport select swd" \
  -f target/xmc4xxx.cfg \
  -c "adapter speed 1000" \
  -c "init" \
  -c "poll" \
  -c "reset run" \
  -c "sleep 1000" \
  -c "poll" \
  -c "halt 0" \
  -c "wait_halt 5000" \
  -c 'if {[lindex [read_memory 0x50004004 32 1] 0] != 0x45004} {error "STOP: expected XMC4500 rev AC"}' \
  -c 'if {[lindex [read_memory 0x58002008 32 1] 0] != 0xa2c001} {error "STOP: unexpected flash identification"}' \
  -c 'foreach v [read_memory 0x58002020 32 3] {if {$v != 0} {error "STOP: unexpected flash protection settings"}}' \
  -c "flash probe 0" \
  -c "flash verify_bank 0 ops243-before-update.bin" \
  -c "flash write_image erase ops243-app.bin 0x0C020000 bin" \
  -c "flash write_image erase ops243-first64k.bin 0x0C000000 bin" \
  -c "flash verify_bank 0 ops243-expected.bin" \
  -c "shutdown"
echo "OpenOCD finished. Confirm final contents match, then follow the power-cycle steps in the guide."
