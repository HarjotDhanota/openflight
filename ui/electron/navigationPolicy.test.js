import { describe, it, expect } from 'vitest';
import { allowedOrigins, isNavigationAllowed } from './navigationPolicy.js';

describe('allowedOrigins', () => {
  it('allows only the page it opened when the launcher lists nothing else', () => {
    expect([...allowedOrigins({}, 'http://localhost:8080/')]).toEqual(['http://localhost:8080']);
  });

  it("adds the launcher's origins: the kiosk and the tester page on the unit's screen", () => {
    const origins = allowedOrigins(
      { OPENFLIGHT_ALLOWED_ORIGINS: 'http://localhost:8765 http://localhost:8080' },
      'http://localhost:8765/'
    );
    expect([...origins].sort()).toEqual(['http://localhost:8080', 'http://localhost:8765']);
  });

  it('accepts commas and full URLs, keeping only their origin', () => {
    const origins = allowedOrigins(
      { OPENFLIGHT_ALLOWED_ORIGINS: 'http://localhost:8080/,http://localhost:8765/#step-7' },
      'http://127.0.0.1:8081/startup-splash.html?target=x'
    );
    expect([...origins].sort()).toEqual([
      'http://127.0.0.1:8081',
      'http://localhost:8080',
      'http://localhost:8765',
    ]);
  });

  it('ignores entries that are not web origins', () => {
    const origins = allowedOrigins(
      { OPENFLIGHT_ALLOWED_ORIGINS: 'file:///etc/passwd not-a-url javascript:alert(1)' },
      'http://localhost:8080/'
    );
    expect([...origins]).toEqual(['http://localhost:8080']);
  });
});

describe('isNavigationAllowed', () => {
  const origins = allowedOrigins(
    { OPENFLIGHT_ALLOWED_ORIGINS: 'http://localhost:8080 http://localhost:8765' },
    'http://localhost:8765/'
  );

  it('lets the window switch between the tester page and the kiosk', () => {
    expect(isNavigationAllowed('http://localhost:8080/?from=tester', origins)).toBe(true);
    expect(isNavigationAllowed('http://localhost:8765/#free-play-photo', origins)).toBe(true);
  });

  it('refuses every other origin, even the same host on another port or name', () => {
    expect(isNavigationAllowed('http://localhost:9000/', origins)).toBe(false);
    expect(isNavigationAllowed('http://127.0.0.1:8080/', origins)).toBe(false);
    expect(isNavigationAllowed('https://localhost:8080/', origins)).toBe(false);
    expect(isNavigationAllowed('https://example.com/', origins)).toBe(false);
  });

  it('refuses non-web and malformed URLs', () => {
    expect(isNavigationAllowed('file:///home/pi/', origins)).toBe(false);
    expect(isNavigationAllowed('about:blank', origins)).toBe(false);
    expect(isNavigationAllowed('', origins)).toBe(false);
    expect(isNavigationAllowed(undefined, origins)).toBe(false);
  });
});
