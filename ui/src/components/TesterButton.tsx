import { useEffect } from 'react';
import { create } from 'zustand';
import { nextTesterPage, openTesterPage, type TesterPageAnswer } from '../utils/testerPage';
import './TesterButton.css';

const POLL_MS = 5000;

const useTesterPageStore = create<{ page: TesterPageAnswer | null }>(() => ({ page: null }));

export function TesterButtonView({ page, onOpen }: { page: TesterPageAnswer | null; onOpen: () => void }) {
  if (!page?.available || !page.url) return null;
  return (
    <div className="tester-link">
      <button type="button" aria-label="Back to the tester page" onClick={onOpen}>
        Tester
      </button>
    </div>
  );
}

async function askTesterPage(): Promise<TesterPageAnswer | null> {
  try {
    const response = await fetch('/api/tester-page', { cache: 'no-store', signal: AbortSignal.timeout(4000) });
    if (response.ok) return await response.json();
    // a kiosk without the tester's hooks has no such route: nothing to link to, ever
    return {
      available: false,
      url: null,
      reason: `the kiosk answered ${response.status}`,
      final: response.status === 404,
    };
  } catch {
    return null;
  }
}

/** Beside the ready light on every tab, while a tester page answers for this unit. */
export function TesterButton() {
  const page = useTesterPageStore((store) => store.page);
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      const answer = await askTesterPage();
      if (!active) return;
      useTesterPageStore.setState((store) => ({ page: nextTesterPage(store.page, answer) }));
      if (!answer?.final) timer = setTimeout(() => void poll(), POLL_MS);
    }
    void poll();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, []);
  return <TesterButtonView page={page} onOpen={() => openTesterPage(page?.url)} />;
}
