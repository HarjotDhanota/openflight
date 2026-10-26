import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { CLUBS_BY_TYPE } from './data/clubs';

const page = readFileSync(new URL('../public/tester.html', import.meta.url), 'utf8');

describe('the tester page', () => {
  it('offers the first club from the kiosk club grid', () => {
    const block = page.match(/<script type="application\/json" id="kiosk-clubs">([\s\S]*?)<\/script>/);
    expect(block).not.toBeNull();
    expect(JSON.parse(block![1])).toEqual(CLUBS_BY_TYPE);
  });

  it('offers Fast and Full radar data, Full unless the tester chose Fast', () => {
    const row = page.match(
      /<div class="seg" role="group" aria-labelledby="radar-data-label" id="radar-data-choice">([\s\S]*?)<\/div>/
    );
    expect(row).not.toBeNull();
    expect(row![1]).toContain('data-radar-data="two_round" aria-pressed="false">Fast</button>');
    expect(row![1]).toContain('data-radar-data="full" aria-pressed="true">Full (complete data)</button>');
    expect(page).toContain("post('/api/tester/radar-data'");
    expect(page).toContain('Fast unavailable: ${kiosk.reason}');
  });
});
