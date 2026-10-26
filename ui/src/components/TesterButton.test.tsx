import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { TesterButtonView } from './TesterButton';
import { nextTesterPage, openedFromTester, returnToTesterPage, type TesterPageAnswer } from '../utils/testerPage';

const noop = () => {};
const AVAILABLE: TesterPageAnswer = { available: true, url: 'http://localhost:8765/', reason: null, final: false };

describe('The Tester button', () => {
  it('shows while a tester page answers for this unit', () => {
    const html = renderToString(<TesterButtonView page={AVAILABLE} onOpen={noop} />);
    expect(html).toContain('>Tester</button>');
    expect(html).toContain('aria-label="Back to the tester page"');
  });
  it('shows nothing on a plain kiosk, or while the tester page does not answer', () => {
    expect(renderToString(<TesterButtonView page={null} onOpen={noop} />)).toBe('');
    expect(
      renderToString(
        <TesterButtonView
          page={{ available: false, url: null, reason: 'the tester page is not answering', final: false }}
          onOpen={noop}
        />
      )
    ).toBe('');
  });
});

describe("Following the kiosk's answer", () => {
  it('takes a new answer as it comes', () => {
    expect(nextTesterPage(null, AVAILABLE)).toEqual(AVAILABLE);
    const gone = { available: false, url: null, reason: 'the tester page is not answering', final: false };
    expect(nextTesterPage(AVAILABLE, gone)).toEqual(gone);
  });
  it('keeps the last page when the kiosk itself stops answering, so the way back stays', () => {
    expect(nextTesterPage(AVAILABLE, null)).toEqual(AVAILABLE);
    expect(nextTesterPage(null, null)).toBeNull();
  });
});

function fakeWindow(search: string, opener: Window | null = null) {
  const assigned: string[] = [];
  return {
    assigned,
    win: { opener, location: { search, assign: (url: string) => assigned.push(url) } } as unknown as Window,
  };
}

describe('A kiosk the tester page showed in its own window', () => {
  it('knows it from its address', () => {
    expect(openedFromTester(fakeWindow('?from=tester').win)).toBe(true);
    expect(openedFromTester(fakeWindow('?from=tester&x=1').win)).toBe(true);
    expect(openedFromTester(fakeWindow('').win)).toBe(false);
    expect(openedFromTester(fakeWindow('?from=elsewhere').win)).toBe(false);
  });
  it('goes back to the tester page when free play stops', () => {
    const { assigned, win } = fakeWindow('?from=tester');
    expect(returnToTesterPage('http://localhost:8765/', win)).toBe(true);
    expect(assigned).toEqual(['http://localhost:8765/']);
  });
  it('stays put in its own window (the tester page is on a phone)', () => {
    const { assigned, win } = fakeWindow('');
    expect(returnToTesterPage('http://localhost:8765/', win)).toBe(false);
    expect(assigned).toEqual([]);
  });
  it('stays put without an address to go back to', () => {
    const { assigned, win } = fakeWindow('?from=tester');
    expect(returnToTesterPage(null, win)).toBe(false);
    expect(assigned).toEqual([]);
  });
});
