import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

/** A fresh setup record path for one E2E backend, so no run reads or writes the unit's own. */
export function uniqueE2eSetupPath(workerId: string | number = process.pid): string {
  return join(mkdtempSync(join(tmpdir(), `openflight-e2e-setup-w${workerId}-`)), 'setup.json');
}
