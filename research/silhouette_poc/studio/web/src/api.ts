import type { Controls, Session, StudioOptions } from './types';

async function json<T>(response: Response): Promise<T> {
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error ?? `HTTP ${response.status}`);
  return payload as T;
}

export async function loadInitial(): Promise<[Session, StudioOptions]> {
  return Promise.all([
    fetch('/api/studio/session').then((response) => json<Session>(response)),
    fetch('/api/studio/options').then((response) => json<StudioOptions>(response)),
  ]);
}

export async function regenerate(controls: Controls): Promise<Session> {
  return fetch('/api/studio/run', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(controls),
  }).then((response) => json<Session>(response));
}
