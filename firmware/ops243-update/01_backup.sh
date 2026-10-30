#!/usr/bin/env bash
# Run from the extracted kit directory. Never overwrites the original backup.
set -euo pipefail
cd -- "$(dirname -- "$0")"
if [[ -e ops243-before-update.bin ]]; then
    echo "STOP: original backup already exists. Keep it; use a fresh kit folder for another sensor." >&2
    exit 1
fi
sudo openocd -f interface/cmsis-dap.cfg \
  -c "transport select swd" \
  -f target/xmc4xxx.cfg \
  -c "adapter speed 1000" \
  -c "init" \
  -c "poll" \
  -c "halt 0" \
  -c "wait_halt 5000" \
  -c 'if {[lindex [read_memory 0x50004004 32 1] 0] != 0x45004} {error "STOP: expected XMC4500 rev AC"}' \
  -c 'if {[lindex [read_memory 0x58002008 32 1] 0] != 0xa2c001} {error "STOP: unexpected flash identification"}' \
  -c "flash probe 0" \
  -c "flash info 0" \
  -c "flash read_bank 0 ops243-before-update.bin" \
  -c "flash verify_bank 0 ops243-before-update.bin" \
  -c "resume" \
  -c "shutdown"
python3 - <<'PY'
from pathlib import Path
import hashlib
p = Path('ops243-before-update.bin')
if p.stat().st_size != 1048576:
    raise SystemExit('STOP: expected a 1 MiB backup')
Path('backup-sha256.txt').write_text(hashlib.sha256(p.read_bytes()).hexdigest() + '  ' + p.name + '\n')
print('Backup complete. Retain the contents-match output and copy this backup somewhere safe.')
PY
