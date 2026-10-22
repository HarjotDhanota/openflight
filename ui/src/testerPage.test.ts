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
});
