import { describe, expect, it } from 'vitest';
import config from '../playwright.config';

interface WebServerConfig {
  command: string;
  url: string;
}

function backendCommand(): string {
  const servers = config.webServer as WebServerConfig[];
  return servers.find((server) => server.url === 'http://127.0.0.1:8080')?.command ?? '';
}

describe('E2E setup record isolation', () => {
  it("starts the backend on its own setup record, never the developer's", () => {
    const match = backendCommand().match(/--setup-file "([^"]+)"/);

    expect(match).not.toBeNull();
    expect(match?.[1]).toContain('openflight-e2e-setup-');
    expect(match?.[1]).toMatch(/setup\.json$/);
  });
});
