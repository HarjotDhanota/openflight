/**
 * Replays a recorded tester session into the real kiosk UI.
 *
 * REPLAY_DIR holds shots.json (the kiosk's own shot dicts, in shot order) and
 * clips/<camera_replay id>.webm. "Simulate shot" emits the next recorded shot,
 * so the session plays back exactly as the kiosk showed it live.
 * REPLAY_PRELOAD=N starts with the first N shots already in the session.
 */

import express from 'express';
import { existsSync, readFileSync } from 'node:fs';
import { createServer } from 'node:http';
import { join } from 'node:path';
import { Server } from 'socket.io';
import type { SessionStats, Shot } from '../src/types/shot.js';
import { registerHandlers } from './handlers.js';
import { MockSession } from './session.js';

const PORT = Number(process.env.MOCK_PORT ?? 8080);
const REPLAY_DIR = process.env.REPLAY_DIR;
if (!REPLAY_DIR) throw new Error('set REPLAY_DIR to an exported session folder');

interface Recording {
  tester_id: string;
  club: string;
  setup: Record<string, unknown> | null;
  target_line: { status?: string; yaw_deg?: number; total_uncertainty_deg?: number; source?: string } | null;
  shots: Shot[];
}

const recording = JSON.parse(readFileSync(join(REPLAY_DIR, 'shots.json'), 'utf-8')) as Recording;

class ReplaySession extends MockSession {
  private next = 0;

  constructor(preload: number) {
    super();
    const first = recording.shots[0];
    const profileId = first?.profile_id ?? 'replay-profile';
    this.profiles = [
      {
        id: profileId,
        name: first?.profile_name ?? recording.tester_id,
        created_at: '2026-01-01T00:00:00Z',
        settings: {},
      },
    ];
    this.activeProfileId = profileId;
    this.club = recording.club;
    while (this.next < Math.min(preload, recording.shots.length)) this.take();
  }

  private take(): Shot {
    const shot = recording.shots[this.next++]!;
    this.shots.push(shot);
    this.triggersTotal += 1;
    this.triggersAccepted += 1;
    return shot;
  }

  override simulateShot(): { shot: Shot; stats: SessionStats } {
    if (this.next >= recording.shots.length) this.reset();
    const shot = this.take();
    console.log(`[replay] shot ${shot.shot_number ?? this.next} of ${recording.shots.length}`);
    return { shot, stats: this.getStats() };
  }

  override clear(): void {
    this.reset();
  }

  override clearProfile(): void {
    this.reset();
  }

  private reset(): void {
    this.shots = [];
    this.next = 0;
  }
}

const app = express();
app.use(express.json());
// the UI reads /api/setup and /api/target-line from the server origin, not through Vite
app.use((_req, res, next) => {
  res.setHeader('Access-Control-Allow-Origin', '*');
  next();
});

// the setup record as the kiosk served it: the session's setup.json, nothing pending
app.get('/api/setup', (_req, res) => {
  if (!recording.setup) {
    res.status(503).json({ error: 'the session recorded no setup' });
    return;
  }
  res.json({
    ...recording.setup,
    live: { camera_available: true, setup_possible: true, setup_blocked_reason: null, setup_fault: false },
    unapplied: {},
  });
});

app.post('/api/setup/:section', (_req, res) => {
  res.status(409).json({ error: 'this is a recorded session; setup is read-only' });
});

app.get('/api/target-line', (_req, res) => {
  const line = recording.target_line ?? {};
  const shown = line.status === 'set';
  res.json({
    status: shown ? 'set' : 'not_set',
    yaw_deg: shown ? line.yaw_deg : null,
    total_uncertainty_deg: shown ? (line.total_uncertainty_deg ?? null) : null,
    source: shown ? (line.source ?? null) : null,
  });
});

// free play's pause/stop bar belongs to the live run, not to a replay
app.get('/api/free-play', (_req, res) => {
  res.json({ enabled: false });
});

app.post('/api/shutdown', (_req, res) => {
  res.json({ status: 'shutting_down' });
});

app.post('/api/camera/replays/:replayId/prepare', (req, res) => {
  const replayId = String(req.params.replayId);
  const shot = recording.shots.find((entry) => entry.camera_replay?.id === replayId);
  if (!shot?.camera_replay || !existsSync(join(REPLAY_DIR, 'clips', `${replayId}.webm`))) {
    res.status(404).json({ error: 'Camera replay was not found' });
    return;
  }
  res.json({
    ...shot.camera_replay,
    video_url: `/api/camera/replays/${encodeURIComponent(replayId)}/video`,
  });
});

app.get('/api/camera/replays/:replayId/video', (req, res) => {
  const replayId = String(req.params.replayId).replace(/[^0-9a-f]/gi, '');
  res.type('video/webm').sendFile(join(REPLAY_DIR, 'clips', `${replayId}.webm`));
});

app.get('/health', (_req, res) => {
  res.json({ ok: true, mock: true, replay: recording.tester_id });
});

const httpServer = createServer(app);
const io = new Server(httpServer, { cors: { origin: '*' } });
registerHandlers(io, new ReplaySession(Number(process.env.REPLAY_PRELOAD ?? 0)));

httpServer.listen(PORT, () => {
  console.log(
    `[replay] ${recording.tester_id}: ${recording.shots.length} shots on http://localhost:${PORT}`
  );
});
