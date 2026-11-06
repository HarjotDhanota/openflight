/* Mocked controls/status; a loopback HTTP fixture serves download bytes only. */
const { test, before, after } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { chromium } = require('@playwright/test');
const html = fs.readFileSync(path.resolve(__dirname, '../public/internal-sync.html'), 'utf8');
let browser, server, origin, currentDownloads;
before(async () => {
  server = http.createServer((request, response) => {
    if (request.url.includes('/file/')) currentDownloads.push(request.url);
    response.writeHead(200, { 'Content-Type': 'application/json' });
    response.end('{}');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  origin = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch({ headless: true });
});
after(async () => { await browser.close(); await new Promise(resolve => server.close(resolve)); });

async function fixture(viewport = { width: 800, height: 400 }, initial = {}) {
  const context = await browser.newContext({ viewport, hasTouch: true });
  const page = await context.newPage();
  const errors = [], commands = [], files = [];
  currentDownloads = files;
  page.on('pageerror', error => errors.push(String(error)));
  const mock = {
    state: { configured: true, session_id: 'a'.repeat(32), phase: 'next', phase_token: 'next-0', attempts: [], job: { action: 'internal_sync', state: 'running', started_at: new Date().toISOString() } },
    network: initial.network || 'ok', advance: false,
  };
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    assert.equal(url.origin, origin);
    if (url.pathname === '/internal-sync.html') return route.fulfill({ contentType: 'text/html', body: html });
    if (url.pathname.includes('/file/')) { files.push(url.pathname); return route.fulfill({ contentType: 'application/json', body: '{}' }); }
    if (route.request().method() === 'POST') {
      const payload = route.request().postDataJSON();
      commands.push(payload);
      if (url.pathname.endsWith('/start')) {
        if (mock.advance) mock.state = { ...mock.state, mode: payload.mode || 'capture', session_id: 'c'.repeat(32), phase: 'switches', phase_token: 'switches-start', job: { action: 'internal_sync', state: 'running', timeout_s: null }, start_available: false };
        if (mock.network === 'command-failure') return route.abort();
        return route.fulfill({ contentType: 'application/json', body: '{}' });
      }
      if (payload.phase_token !== mock.state.phase_token) return route.fulfill({ status: 409, contentType: 'application/json', body: JSON.stringify({ error: 'Session changed; refresh before retrying' }) });
      if (mock.advance) {
        const phase = { switches_set: 'reset', reset_pressed: 'next', stop: 'paused', retry: 'learning_background', end: 'stopped', restored: 'stopped' }[payload.command];
        if (phase) mock.state = { ...mock.state, phase, phase_token: phase + commands.length, resources_released: true };
        if (payload.command === 'stop') Object.assign(mock.state, { retry_available: true, retry_reason: 'RF stopped and UART verified', resources_released: false });
        if (payload.command === 'retry') Object.assign(mock.state, { retry_available: false, iwr: null });
        if (payload.command === 'end') Object.assign(mock.state, { retry_available: false, iwr: null });
        if (payload.command === 'restored') mock.state.normal_boot_restore_needed = false;
      }
      if (mock.network === 'command-failure') return route.abort();
      return route.fulfill({ contentType: 'application/json', body: '{}' });
    }
    if (mock.network === 'offline') return route.abort();
    if (mock.network === 'stalled') return;
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify(mock.state) });
  });
  await page.goto(origin + '/internal-sync.html');
  if (!initial.network) await page.waitForFunction(() => document.querySelector('#connection').textContent === 'Status connected');
  async function phase(name, extra = {}) {
    mock.state = { ...mock.state, phase: name, phase_token: name + Math.random(), ...extra };
    await page.evaluate(() => refresh());
    await page.waitForFunction(() => document.querySelector('#phase').textContent !== 'Loading status…');
  }
  return { context, page, mock, commands, files, errors, phase };
}

function saved(index, status = 'captured_unqualified') {
  return { name: `attempt-${String(index).padStart(3, '0')}`, status, error: status === 'partial' ? 'TimeoutError: no IWR detection' : null, iwr_shot_id: index, camera: { clips: [{ metadata: 'camera/capture/metadata.json' }] } };
}

function healthyIwr() {
  return { capture: { active: '1' }, trigger: { enabled: '1', latched: '0' }, ball: { state: 'locked', follow: '1', reason: 'none', bin: '32', locks: '1', releases: '0' }, candidate_loss: null, trigger_observed: false };
}

const inspectionArtifacts = ['RESULT.json', 'iwr-exchanges.json', 'ball-evidence/reply.bin', 'ball-evidence/exchange.json', 'ball-evidence/decoded.json', 'ball-evidence/coverage.json'];
function inspected(index, extra = {}) {
  return { name: `attempt-${String(index).padStart(3, '0')}`, diagnostic: true, status: 'diagnostic_saved_unqualified', outcome: 'host_observed_lock', artifacts: inspectionArtifacts, ...extra };
}

for (const [width, height] of [[1024, 600], [800, 480], [800, 400]]) {
  test(`inspection starts explicitly and stays no-swing through retained or malformed states at ${width}x${height}`, async () => {
    const f = await fixture({ width, height });
    try {
      await f.phase('idle', { session_id: null, mode: 'capture', inspection_configured: false, start_available: true, job: null });
      assert.equal(await f.page.locator('#inspect').isDisabled(), true);
      assert.match(await f.page.locator('#inspection-availability').innerText(), /unavailable.*firmware package/i);
      assert.equal(await f.page.locator('#start').isEnabled(), true);
      await f.phase('stopped', { session_id: 'a'.repeat(32), inspection_configured: true, start_available: false, start_reason: 'Restore normal boot first', resources_released: true, normal_boot_restore_needed: true });
      assert.equal(await f.page.locator('#inspect').isDisabled(), true);
      assert.match(await f.page.locator('#inspection-availability').innerText(), /Restore normal boot/);
      await f.phase('stopped', { start_available: true, normal_boot_restore_needed: false });
      assert.equal(await f.page.locator('#inspect').isEnabled(), true);
      const button = await f.page.locator('#inspect').boundingBox();
      assert.ok(button.height >= 43 && button.y + button.height <= height);
      assert.deepEqual(f.commands, []);
      f.mock.advance = true;
      await f.page.locator('#inspect').tap();
      await f.page.waitForFunction(() => document.querySelector('#heading').textContent === 'Ball-lock inspection');
      assert.deepEqual(f.commands, [{ mode: 'ball_lock_inspection' }]);
      assert.equal(await f.page.locator('#inspect').isVisible(), false);
      const retained = Array.from({ length: 4 }, (_, i) => inspected(i + 1));
      for (const name of ['next', 'preparing_attempt', 'learning_background', 'place_ball', 'waiting_ball', 'inspection_locked', 'preparing_sensors', 'armed', 'candidate_lost', 'saving', 'paused', 'resuming']) {
        await f.phase(name, { mode: 'ball_lock_inspection', attempts: retained, iwr: healthyIwr(), retry_available: true, resume_available: true, planned_activity_index: 5 });
        assert.match(await f.page.locator('#instruction').innerText(), /do not swing/i, name);
        const visible = await f.page.locator('main').innerText();
        assert.doesNotMatch(visible, /dry swing|real strike|Three planned activities|Planned slot|Wait for Armed|Make one|One dry swing|Resume loaded session|Prepare real|Retry real/i, name);
        assert.equal(await f.page.locator('#resume').isVisible(), false);
        assert.equal(await f.page.locator('#start').isVisible(), false);
        if (name === 'next') {
          assert.equal(await f.page.locator('#confirm').innerText(), 'Prepare inspection');
          assert.equal(await f.page.locator('#confirm').isEnabled(), true);
        }
        if (name === 'place_ball') assert.match(await f.page.locator('#message').innerText(), /Keep the lane empty/);
        if (name === 'paused') {
          assert.equal(await f.page.locator('#retry').innerText(), 'Retry inspection');
          assert.equal(await f.page.locator('#retry').isEnabled(), true);
        }
      }
      await f.phase('next', { attempts: [], iwr: null });
      await f.page.evaluate(() => scrollTo(0, 0));
      for (const id of ['confirm', 'stop', 'end']) {
        const box = await f.page.locator('#' + id).boundingBox();
        assert.ok(box.y >= 0 && box.y + box.height <= height && box.height >= 43, `${id} visible finger control`);
      }
      if (process.env.OPENFLIGHT_UI_EVIDENCE) {
        fs.mkdirSync(process.env.OPENFLIGHT_UI_EVIDENCE, { recursive: true });
        await f.page.screenshot({ path: path.join(process.env.OPENFLIGHT_UI_EVIDENCE, `inspection-next-${width}x${height}.png`) });
      }
      await f.page.reload();
      await f.page.waitForFunction(() => document.querySelector('#connection').textContent === 'Status connected');
      assert.equal(await f.page.locator('#confirm').innerText(), 'Prepare inspection');
      assert.equal(f.commands.length, 1);
      await f.phase('armed', { job: { action: 'internal_sync', state: 'idle' } });
      assert.match(await f.page.locator('#instruction').innerText(), /owner unavailable.*do not swing/i);
      assert.equal(await f.page.locator('#stop').isDisabled(), true);
      await f.phase('armed', { job: { action: 'internal_sync', state: 'running', started_at: new Date(Date.now() - 60000).toISOString(), timeout_s: 10 } });
      assert.match(await f.page.locator('#instruction').innerText(), /time limit reached.*do not swing/i);
      f.mock.network = 'offline';await f.page.evaluate(() => refresh());
      assert.match(await f.page.locator('#instruction').innerText(), /Status unavailable.*do not swing/i);
      assert.equal(await f.page.locator('#end').isDisabled(), true);
      assert.equal(await f.page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      assert.deepEqual(f.errors, []);
    } finally { await f.context.close(); }
  });

  test(`inspection exports only reported files and supports drag then tap at ${width}x${height}`, async () => {
    const f = await fixture({ width, height });
    try {
      const partial = inspected(1, { status: 'diagnostic_failed', outcome: 'operator_stop', artifacts: inspectionArtifacts.slice(0, 4), cleanup_error: 'ValueError: short reply' });
      await f.phase('paused', { mode: 'ball_lock_inspection', retry_available: true, attempts: [partial, inspected(2, { outcome: 'timeout' }), ...Array.from({ length: 10 }, (_, i) => inspected(i + 3))] });
      const first = f.page.locator('.attempt').first();
      assert.match(await first.innerText(), /Failed\/partial inspection.*operator Stop/s);
      assert.match(await first.innerText(), /short reply/);
      assert.equal(await first.locator('a').count(), 4);
      assert.equal(await first.getByRole('link', { name: 'Detector decoded.json' }).count(), 0);
      assert.equal(await f.page.locator('.attempt').nth(1).locator('a').count(), 6);
      const list = f.page.locator('#attempts');await list.scrollIntoViewIfNeeded();
      const box = await list.boundingBox();
      await f.page.mouse.move(box.x + 40, box.y + 180);await f.page.mouse.down();
      await f.page.mouse.move(box.x + 40, box.y + 20, { steps: 12 });await f.page.mouse.up();
      assert.ok(await list.evaluate(el => el.scrollTop) > 0);
      assert.equal(f.files.length, 0);assert.deepEqual(f.commands, []);
      await list.evaluate(el => { el.scrollTop = 0; });
      for (const [label, filename] of [['Attempt record', 'RESULT.json'], ['IWR exchanges', 'iwr-exchanges.json'], ['Detector reply.bin', 'reply.bin'], ['Detector exchange.json', 'exchange.json']]) {
        const download = f.page.waitForEvent('download');await first.getByRole('link', { name: label, exact: true }).tap();
        const file = await download;assert.equal(file.suggestedFilename(), filename);assert.equal(fs.readFileSync(await file.path(), 'utf8'), '{}');
      }
      assert.equal(f.files.length, 4);
      assert.equal(f.page.url(), origin + '/internal-sync.html');
      if (process.env.OPENFLIGHT_UI_EVIDENCE) await f.page.screenshot({ path: path.join(process.env.OPENFLIGHT_UI_EVIDENCE, `inspection-files-${width}x${height}.png`) });
      assert.deepEqual(f.errors, []);
    } finally { await f.context.close(); }
  });
}

test('inspection uncertain Prepare and Stop retain explicit safe Retry and End safeguards', async () => {
  const f = await fixture();
  try {
    await f.phase('next', { mode: 'ball_lock_inspection', planned_activity_index: 9, attempts: [inspected(1)] });
    f.mock.network = 'command-failure';await f.page.locator('#confirm').tap();
    await f.page.waitForFunction(() => document.querySelector('#error').textContent.includes('Do not repeat'));
    f.mock.network = 'ok';await f.page.evaluate(() => refresh());
    assert.equal(await f.page.locator('#confirm').isDisabled(), true);assert.equal(f.commands.length, 1);
    assert.equal(await f.page.locator('#stop').isEnabled(), true);
    f.mock.advance = true;await f.page.locator('#stop').tap();
    await f.page.waitForFunction(() => document.querySelector('#phase').textContent.includes('Attempt paused'));
    assert.equal(await f.page.locator('#retry').isEnabled(), true);
    await f.page.locator('#retry').dblclick();
    await f.page.waitForFunction(() => document.querySelector('#phase').textContent.includes('Learning'));
    assert.deepEqual(f.commands.map(row => row.command), ['next', 'stop', 'retry']);
    await f.phase('paused', { retry_available: false, retry_reason: 'UART framing uncertain' });
    assert.equal(await f.page.locator('#retry').isDisabled(), true);
    assert.match(await f.page.locator('#message').innerText(), /UART framing uncertain/);
    await f.page.locator('#end').tap();
    await f.page.waitForFunction(() => document.querySelector('#phase').textContent.includes('Stopped'));
    assert.equal(await f.page.locator('#resume').isVisible(), false);
    assert.deepEqual(f.commands.map(row => row.command), ['next', 'stop', 'retry', 'end']);
  } finally { await f.context.close(); }
});

for (const network of ['offline', 'stalled']) {
  test(`unknown session with initial ${network} status never exposes capture swing instructions`, async () => {
    const f = await fixture({ width: 800, height: 400 }, { network });
    try {
      assert.match(await f.page.locator('#instruction').innerText(), /do not swing/i);
      assert.equal(await f.page.locator('#capture-plan').isVisible(), false);
      assert.equal(await f.page.locator('#saved-note').isVisible(), false);
      assert.equal(await f.page.locator('#start').isDisabled(), true);
      assert.equal(await f.page.locator('#inspect').isDisabled(), true);
      assert.doesNotMatch(await f.page.locator('main').innerText(), /dry swing|real strike|Wait for Armed|Three planned activities|Planned slot/i);
      assert.deepEqual(f.commands, []);
    } finally { await f.context.close(); }
  });
}

for (const [width, height] of [[1024, 600], [800, 480], [800, 400]]) {
  test(`reloaded stopping and preparing attempt phases stay readable and quiet at ${width}x${height}`, async () => {
    const f = await fixture({ width, height });
    try {
      for (const [phase, label] of [['stopping', 'Stopping attempt'], ['preparing_attempt', 'Preparing attempt']]) {
        await f.phase(phase, { iwr: healthyIwr(), retry_available: false });
        await f.page.reload();
        await f.page.waitForFunction(() => document.querySelector('#connection').textContent === 'Status connected');
        assert.equal(await f.page.locator('#phase').innerText(), label);
        assert.match(await f.page.locator('#instruction').innerText(), /Do not swing|Hold still/i);
        assert.doesNotMatch(await f.page.locator('section[aria-live]').innerText(), /Within 30 seconds|One dry swing/i);
        assert.equal(await f.page.locator('#retry').isVisible(), false);
        assert.equal(await f.page.locator('#confirm').isVisible(), false);
        assert.deepEqual(f.commands, []);
      }
      assert.deepEqual(f.errors, []);
    } finally { await f.context.close(); }
  });
}

for (const [width, height] of [[1024, 600], [800, 480], [800, 400]]) {
  test(`Stop, explicit safe Retry and End preserve the planned slot at ${width}x${height}`, async () => {
    const f = await fixture({ width, height });
    try {
      assert.equal(await f.page.locator('#retry').count(), 1);
      f.mock.advance = true;
      await f.phase('armed', { iwr: healthyIwr(), planned_activity_index: 1, normal_boot_restore_needed: true });
      await f.page.locator('#stop').tap();
      await f.page.waitForFunction(() => document.querySelector('#phase').textContent === 'Attempt paused');
      assert.match(await f.page.locator('#instruction').innerText(), /do not swing/i);
      assert.deepEqual(f.commands.map(row => row.command), ['stop']);
      const aborted = { ...saved(1, 'partial'), aborted: true, planned_activity_index: 1 };
      await f.phase('paused', { attempts: [aborted], planned_activity_index: 1, retry_available: true, retry_reason: 'Stopped safely', iwr: null });
      assert.match(await f.page.locator('#retry').innerText(), /Retry dry swing/);
      assert.equal(await f.page.locator('#retry').isEnabled(), true);
      assert.match(await f.page.locator('#progress').innerText(), /Planned slot 1 of 3/);
      assert.match(await f.page.locator('.attempt').innerText(), /Planned dry swing/);
      const retry = await f.page.locator('#retry').boundingBox();
      const end = await f.page.locator('#end').boundingBox();
      assert.ok(retry.y + retry.height <= height && end.y + end.height <= height);
      if (process.env.OPENFLIGHT_UI_EVIDENCE) {
        fs.mkdirSync(process.env.OPENFLIGHT_UI_EVIDENCE, { recursive: true });
        await f.page.screenshot({ path: path.join(process.env.OPENFLIGHT_UI_EVIDENCE, `paused-${width}x${height}.png`) });
      }
      await f.page.locator('#retry').dblclick();
      await f.page.waitForFunction(() => document.querySelector('#phase').textContent === 'Learning the empty hitting area');
      assert.deepEqual(f.commands.map(row => row.command), ['stop', 'retry']);
      assert.equal(await f.page.locator('#retry').isVisible(), false);
      await f.phase('paused', { retry_available: false, retry_reason: 'UART framing uncertain' });
      assert.equal(await f.page.locator('#retry').isDisabled(), true);
      assert.match(await f.page.locator('#message').innerText(), /UART framing uncertain/);
      await f.phase('paused', { retry_available: true, planned_activity_index: 2, attempts: [saved(1), { ...saved(2, 'partial'), planned_activity_index: 2 }] });
      assert.equal(await f.page.locator('#retry').isDisabled(), true);
      assert.match(await f.page.locator('#message').innerText(), /review/i);
      await f.phase('paused', { retry_available: true, planned_activity_index: 1, attempts: [aborted] });
      await f.page.reload();
      await f.page.waitForFunction(() => document.querySelector('#connection').textContent === 'Status connected');
      assert.equal(await f.page.locator('#retry').isEnabled(), true);
      assert.deepEqual(f.commands.map(row => row.command), ['stop', 'retry']);
      f.mock.network = 'offline';
      await f.page.evaluate(() => refresh());
      assert.equal(await f.page.locator('#retry').isDisabled(), true);
      assert.equal(await f.page.locator('#end').isDisabled(), true);
      f.mock.network = 'ok';
      await f.phase('paused');
      await f.page.locator('#end').tap();
      await f.page.waitForFunction(() => document.querySelector('#phase').textContent === 'Stopped');
      assert.deepEqual(f.commands.map(row => row.command), ['stop', 'retry', 'end']);
      assert.equal(await f.page.locator('#retry').isVisible(), false);
      assert.equal(await f.page.locator('#confirm').isVisible(), false);
      assert.equal(await f.page.locator('#stop').isDisabled(), true);
      assert.equal(await f.page.locator('#restore').isVisible(), true);
      assert.deepEqual(f.errors, []);
    } finally { await f.context.close(); }
  });
}

test('Retry uses the current phase token and never repeats a refused or uncertain command', async () => {
  const f = await fixture();
  try {
    assert.equal(await f.page.locator('#retry').count(), 1);
    await f.phase('paused', { planned_activity_index: 1, retry_available: true });
    f.mock.state.phase_token = 'new-server-token';
    await f.page.locator('#retry').tap();
    assert.equal(f.commands.length, 1);
    assert.notEqual(f.commands[0].phase_token, f.mock.state.phase_token);
    await f.page.waitForFunction(() => document.querySelector('#error').textContent.includes('Session changed'));
    assert.match(await f.page.locator('#error').innerText(), /Session changed/);
    await f.page.evaluate(() => refresh());
    assert.equal(f.commands.length, 1);
    f.mock.network = 'command-failure';
    await f.page.locator('#retry').tap();
    await f.page.waitForFunction(() => document.querySelector('#error').textContent.includes('Do not repeat'));
    assert.equal(f.commands.length, 2);
    f.mock.network = 'ok';
    await f.page.evaluate(() => refresh());
    assert.equal(await f.page.locator('#retry').isDisabled(), true);
    assert.equal(f.commands.length, 2);
    assert.match(await f.page.locator('#error').innerText(), /Do not repeat.*Retry/);
    assert.equal(await f.page.locator('#end').isEnabled(), true);
  } finally { await f.context.close(); }
});

test('retained paused or armed status without its live owner never invites Retry or swing', async () => {
  const f = await fixture();
  try {
    for (const phase of ['paused', 'armed']) {
      for (const job of [{ action: 'internal_sync', state: 'idle' }, { action: 'preflight', state: 'running' }]) {
        await f.phase(phase, { job, iwr: healthyIwr(), retry_available: true, planned_activity_index: 1 });
        assert.doesNotMatch(await f.page.locator('section[aria-live]').innerText(), /Within 30 seconds|One dry swing|Tap Retry/i);
        assert.match(await f.page.locator('#instruction').innerText(), /do not swing/i);
        assert.equal(await f.page.locator('#retry').isEnabled(), false);
        assert.equal(await f.page.locator('#end').isEnabled(), false);
        await f.page.evaluate(() => refresh());
        assert.deepEqual(f.commands, []);
      }
    }
  } finally { await f.context.close(); }
});

test('pending and uncertain Stop quiet armed guidance; deliberate End can escalate', async () => {
  const f = await fixture();
  try {
    await f.phase('armed', { iwr: healthyIwr() });
    await f.page.locator('#stop').tap();
    assert.doesNotMatch(await f.page.locator('section[aria-live]').innerText(), /Within 30 seconds|make one swing|One dry swing/i);
    await f.phase('armed', { iwr: healthyIwr() });
    assert.match(await f.page.locator('#instruction').innerText(), /Hold still/);
    assert.doesNotMatch(await f.page.locator('section[aria-live]').innerText(), /Within 30 seconds|One dry swing/i);
    f.mock.network = 'command-failure';
    await f.page.locator('#end').tap();
    assert.deepEqual(f.commands.map(row => row.command), ['stop', 'end']);
    f.mock.network = 'ok';
    await f.page.evaluate(() => refresh());
    assert.match(await f.page.locator('#instruction').innerText(), /Hold still/);
    assert.equal(await f.page.locator('#end').isDisabled(), true);
    assert.doesNotMatch(await f.page.locator('section[aria-live]').innerText(), /Within 30 seconds|One dry swing/i);
    await f.phase('paused', { retry_available: true });
    assert.match(await f.page.locator('#instruction').innerText(), /Hold still.*ending/i);
    assert.equal(await f.page.locator('#retry').isEnabled(), false);
    assert.doesNotMatch(await f.page.locator('#message').innerText(), /Tap Retry/);
    assert.deepEqual(f.errors, []);
  } finally { await f.context.close(); }
});

test('the next dry-swing note uses the completed activity after aborted setup files', async () => {
  const f = await fixture();
  try {
    const aborted = { ...saved(1, 'partial'), aborted: true, planned_activity_index: 1 };
    await f.phase('next', { attempts: [aborted, { ...saved(2), planned_activity_index: 1 }], planned_activity_index: 2 });
    assert.doesNotMatch(await f.page.locator('#message').innerText(), /Dry-swing partial\/timeout retained/);
    assert.match(await f.page.locator('#confirm').innerText(), /Prepare real strike/);
    assert.deepEqual(f.commands, []);
  } finally { await f.context.close(); }
});

test('a refused stale Stop remains quiet while current status still says armed', async () => {
  const f = await fixture();
  try {
    await f.phase('armed', { iwr: healthyIwr() });
    f.mock.state.phase_token = 'advanced-before-stop';
    await f.page.locator('#stop').tap();
    await f.page.waitForFunction(() => document.querySelector('#error').textContent.includes('Session changed'));
    await f.page.evaluate(() => refresh());
    assert.match(await f.page.locator('#instruction').innerText(), /Hold still/);
    assert.doesNotMatch(await f.page.locator('section[aria-live]').innerText(), /Within 30 seconds|One dry swing/i);
    assert.equal(await f.page.locator('#end').isEnabled(), true);
    assert.deepEqual(f.commands.map(row => row.command), ['stop']);
    f.mock.advance = true;
    await f.page.locator('#stop').tap();
    await f.page.waitForFunction(() => document.querySelector('#phase').textContent === 'Attempt paused');
    assert.deepEqual(f.commands.map(row => row.command), ['stop', 'stop']);
    assert.equal(f.commands[1].phase_token, 'advanced-before-stop');
  } finally { await f.context.close(); }
});

for (const [width, height] of [[1024, 600], [800, 480], [800, 400]]) {
  test(`guided three-slot flow, refresh and visible actions at ${width}x${height}`, async () => {
    const f = await fixture({ width, height });
    const { page, mock, commands, phase } = f;
    try {
      assert.match(await page.locator('#instruction').innerText(), /Remove the ball/);
      assert.match(await page.locator('#message').innerText(), /Do not put the ball back until asked/);
      for (const id of ['confirm', 'stop']) {
        const box = await page.locator('#' + id).boundingBox();
        assert.ok(box.y >= 0 && box.y + box.height <= height, `${id} above fold`);
      }
      for (let slot = 1; slot <= 3; slot++) {
        await page.locator('#confirm').tap();
        assert.equal(commands.length, slot);
        assert.equal(commands.at(-1).command, 'next');
        assert.equal(await page.locator('#confirm').isDisabled(), true);
        await phase('learning_background');
        assert.match(await page.locator('#instruction').innerText(), /lane empty/);
        await phase('place_ball');
        assert.match(await page.locator('#instruction').innerText(), /Place the ball/);
        await phase('preparing_sensors');
        assert.match(await page.locator('#instruction').innerText(), /Do not swing yet/);
        await phase('armed', { iwr: healthyIwr() });
        assert.match(await page.locator('#instruction').innerText(), slot === 1 ? /dry swing.*do not hit/ : /one real strike/);
        assert.match(await page.locator('#message').innerText(), /30 seconds/);
        await page.evaluate(() => scrollTo(0, 0));
        const instruction = await page.locator('#instruction').boundingBox();
        assert.ok(instruction.y + instruction.height < height);
        if (slot === 1 && process.env.OPENFLIGHT_UI_EVIDENCE) {
          fs.mkdirSync(process.env.OPENFLIGHT_UI_EVIDENCE, { recursive: true });
          await page.screenshot({ path: path.join(process.env.OPENFLIGHT_UI_EVIDENCE, `armed-${width}x${height}.png`) });
        }
        await phase('armed', { iwr: { capture: { active: '0' }, trigger: { enabled: '1', latched: '1' }, ball: { state: 'locked', bin: '32' } } });
        assert.match(await page.locator('#instruction').innerText(), /Hold still/);
        assert.match(await page.locator('#message').innerText(), /Do not swing again/);
        assert.doesNotMatch(await page.locator('section[aria-live]').innerText(), /Within 30 seconds|make one swing|One dry swing/i);
        mock.state.iwr = null;
        await phase('saving');
        assert.match(await page.locator('#instruction').innerText(), /saving original files/);
        mock.state.attempts.push(saved(slot, slot === 1 ? 'partial' : 'captured_unqualified'));
        await phase('next');
        if (slot === 1) assert.match(await page.locator('#message').innerText(), /timeout retained/);
      }
      assert.equal(await page.locator('#confirm').isDisabled(), true);
      assert.equal(await page.locator('#stop').isEnabled(), true);
      assert.match(await page.locator('#message').innerText(), /first two strikes need review/);
      await page.reload();
      await page.waitForFunction(() => document.querySelector('#confirm').disabled && document.querySelector('#progress').textContent.includes('3 saved'));
      assert.equal(commands.length, 3);
      assert.equal(await page.locator('.attempt').count(), 3);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      assert.deepEqual(f.errors, []);
    } finally { await f.context.close(); }
  });
}

for (const [width, height] of [[1024, 600], [800, 480], [800, 400]]) {
  test(`candidate loss stays quiet and later trigger wins at ${width}x${height}`, async () => {
    const f = await fixture({ width, height });
    try {
      for (const iwr of [null, { ...healthyIwr(), capture: { active: '0' } }, { ...healthyIwr(), trigger: { enabled: '0', latched: '0' } }, { ...healthyIwr(), ball: { ...healthyIwr().ball, reason: 'gone' } }]) {
        await f.phase('armed', { iwr });
        assert.doesNotMatch(await f.page.locator('section[aria-live]').innerText(), /Within 30 seconds|make one swing|One dry swing/i);
        assert.match(await f.page.locator('#instruction').innerText(), /do not swing/i);
      }
      const lost = { ...healthyIwr(), candidate_loss: { reason: 'ball lock changed' } };
      await f.phase('candidate_lost', { iwr: lost });
      assert.match(await f.page.locator('#instruction').innerText(), /Candidate lost.*do not swing/i);
      await f.phase('armed', { iwr: lost });
      assert.match(await f.page.locator('#instruction').innerText(), /do not swing/i);
      await f.phase('candidate_lost', { iwr: { ...lost, trigger_observed: true } });
      assert.match(await f.page.locator('#instruction').innerText(), /Hold still.*trigger observed/i);
      assert.match(await f.page.locator('#message').innerText(), /Do not swing again/);
      assert.doesNotMatch(await f.page.locator('section[aria-live]').innerText(), /Within 30 seconds|make one swing|One dry swing/i);
      await f.phase('candidate_lost', { iwr: { ...lost, trigger: { enabled: '1', latched: '1' } } });
      assert.match(await f.page.locator('#instruction').innerText(), /Hold still.*trigger observed/i);
      assert.deepEqual(f.commands, []);
      assert.deepEqual(f.errors, []);
    } finally { await f.context.close(); }
  });
}

test('partial strike pauses; session identity and saved count survive reload', async () => {
  const f = await fixture();
  try {
    await f.phase('next', { attempts: [saved(1), saved(2, 'partial')] });
    assert.equal(await f.page.locator('#confirm').isDisabled(), true);
    assert.match(await f.page.locator('#message').innerText(), /do not keep retrying/);
    await f.page.reload();
    await f.page.waitForFunction(() => document.querySelector('#progress').textContent.includes('2 saved'));
    assert.equal(await f.page.locator('#confirm').isDisabled(), true);
    await f.phase('next', { session_id: 'b'.repeat(32), attempts: [] });
    assert.equal(await f.page.locator('#confirm').innerText(), 'Prepare dry swing');
    assert.equal(await f.page.locator('#confirm').isEnabled(), true);
    assert.deepEqual(f.commands, []);
  } finally { await f.context.close(); }
});

for (const phaseName of ['preparing_sensors', 'armed']) {
  test(`retained waiting/bin0 state cannot imply a confirmed ball or invite a swing during ${phaseName}`, async () => {
    const f = await fixture();
    try {
      await f.phase(phaseName, { iwr: { capture: { active: '1' }, trigger: { enabled: '1', latched: '0' }, ball: { state: 'waiting', follow: '1', bin: '0' } } });
      assert.doesNotMatch(await f.page.locator('section[aria-live]').innerText(), /The ball is locked|Within 30 seconds|make one swing|One dry swing/i);
      assert.match(await f.page.locator('#instruction').innerText(), /Do not swing|Hold still/i);
      assert.deepEqual(f.commands, []);
    } finally { await f.context.close(); }
  });
}

test('pending command, offline and stalled status lock actions; Stop stays deliberate', async () => {
  const f = await fixture();
  try {
    await f.page.locator('#confirm').dblclick();
    assert.equal(f.commands.length, 1);
    await f.page.waitForTimeout(900);
    assert.equal(await f.page.locator('#confirm').isDisabled(), true);
    assert.equal(await f.page.locator('#stop').isEnabled(), true);
    await f.page.locator('#stop').dblclick();
    assert.equal(f.commands.length, 2);
    assert.equal(f.commands.at(-1).command, 'stop');
    assert.equal(await f.page.locator('#stop').isDisabled(), true);
    f.mock.network = 'offline';
    await f.page.evaluate(() => refresh());
    assert.equal(await f.page.locator('#stop').isDisabled(), true);
    assert.equal(await f.page.locator('#confirm').isDisabled(), true);
    f.mock.network = 'ok';
    await f.phase('armed');
    assert.equal(await f.page.locator('#error').innerText(), '');
    f.mock.network = 'stalled';
    await f.page.waitForTimeout(4500);
    assert.equal(await f.page.locator('#stop').isDisabled(), true);
    assert.match(await f.page.locator('#instruction').innerText(), /Status unavailable/);
    assert.doesNotMatch(await f.page.locator('section[aria-live]').innerText(), /Within 30 seconds|make one swing|One dry swing/i);
    assert.equal(f.commands.length, 2);
  } finally { await f.context.close(); }
});

test('uncertain command response is not repeated after successful status polling', async () => {
  const f = await fixture();
  try {
    f.mock.network = 'command-failure';
    await f.page.locator('#confirm').tap();
    await f.page.waitForFunction(() => document.querySelector('#error').textContent.includes('Do not repeat Prepare'));
    f.mock.network = 'ok';
    await f.page.evaluate(() => refresh());
    assert.match(await f.page.locator('#error').innerText(), /Do not repeat Prepare/);
    assert.equal(await f.page.locator('#confirm').isDisabled(), true);
    assert.equal(f.commands.length, 1);
    assert.equal(await f.page.locator('#stop').isEnabled(), true);
  } finally { await f.context.close(); }
});

test('switch/reset confirmations, Stop, restoration and parent deadline remain explicit', async () => {
  const f = await fixture();
  try {
    f.mock.advance = true;
    await f.phase('switches', { message: 'Set S1 switches; do not press RESET yet.', normal_boot_restore_needed: true });
    await f.page.locator('#confirm').tap();
    await f.page.waitForFunction(() => document.querySelector('#confirm').textContent === 'RESET pressed; waited at least one second');
    await f.page.locator('#confirm').tap();
    await f.page.waitForFunction(() => document.querySelector('#confirm').textContent === 'Prepare dry swing');
    await f.page.locator('#end').tap();
    await f.page.waitForFunction(() => document.querySelector('#phase').textContent === 'Stopped');
    assert.match(await f.page.locator('#message').innerText(), /new RAM load unsupervised/);
    await f.page.locator('#restored').tap();
    assert.deepEqual(f.commands.map(row => row.command), ['switches_set', 'reset_pressed', 'end', 'restored']);
    assert.equal(await f.page.locator('#start').isVisible(), false);
    await f.phase('next', { job: { action: 'internal_sync', state: 'stopped' } });
    assert.equal(await f.page.locator('#confirm').isVisible(), false);
    await f.phase('next', { job: { action: 'internal_sync', state: 'error' } });
    assert.match(await f.page.locator('#phase').innerText(), /error/);
    await f.phase('armed', { job: { action: 'internal_sync', state: 'running', started_at: new Date(Date.now() - 3600001).toISOString() } });
    assert.match(await f.page.locator('#instruction').innerText(), /time limit reached/);
    assert.equal(await f.page.locator('#confirm').isDisabled(), true);
    assert.equal(await f.page.locator('#stop').isEnabled(), true);
    assert.doesNotMatch(await f.page.locator('section[aria-live]').innerText(), /Within 30 seconds|make one swing|One dry swing/i);
  } finally { await f.context.close(); }
});

test('reported null, numeric and legacy timeouts control expiry without automatic commands', async () => {
  const f = await fixture();
  try {
    const old = new Date(Date.now() - 4 * 3600000).toISOString();
    await f.phase('next', { job: { action: 'internal_sync', state: 'running', started_at: old, timeout_s: null } });
    assert.match(await f.page.locator('#deadline').innerText(), /No overall session expiry.*finite capture limits/);
    assert.equal(await f.page.locator('#confirm').isEnabled(), true);
    const next = await f.page.locator('#confirm').boundingBox();
    assert.ok(next.y + next.height <= 400);
    await f.phase('armed', { job: { action: 'internal_sync', state: 'running', started_at: old, timeout_s: 30 } });
    assert.match(await f.page.locator('#instruction').innerText(), /time limit reached/);
    assert.equal(await f.page.locator('#confirm').isVisible(), false);
    assert.doesNotMatch(await f.page.locator('section[aria-live]').innerText(), /Within 30 seconds|make one swing|One dry swing/i);
    await f.phase('next', { job: { action: 'internal_sync', state: 'running', started_at: new Date(Date.now() - 60000).toISOString(), timeout_s: 120 } });
    assert.match(await f.page.locator('#deadline').innerText(), /1 min left/);
    assert.equal(await f.page.locator('#confirm').isEnabled(), true);
    await f.phase('next', { job: { action: 'internal_sync', state: 'running', started_at: old } });
    assert.equal(await f.page.locator('#confirm').isDisabled(), true);
    assert.match(await f.page.locator('#deadline').innerText(), /time limit reached/);
    assert.deepEqual(f.commands, []);
  } finally { await f.context.close(); }
});

test('Resume requires an explicit eligible-session tap and waits for checked runtime status', async () => {
  const f = await fixture();
  try {
    await f.phase('stopped', { resume_available: true, resources_released: true, normal_boot_restore_needed: true, job: { action: 'internal_sync', state: 'stopped', timeout_s: null } });
    assert.equal(await f.page.locator('#resume').isEnabled(), true);
    const resume = await f.page.locator('#resume').boundingBox();
    assert.ok(resume.y + resume.height <= 400);
    if (process.env.OPENFLIGHT_UI_EVIDENCE) {
      fs.mkdirSync(process.env.OPENFLIGHT_UI_EVIDENCE, { recursive: true });
      await f.page.screenshot({ path: path.join(process.env.OPENFLIGHT_UI_EVIDENCE, 'resume-800x400.png') });
    }
    assert.match(await f.page.locator('#message').innerText(), /do not press RESET/);
    assert.deepEqual(f.commands, []);
    await f.page.locator('#resume').dblclick();
    assert.equal(f.commands.length, 1);
    assert.deepEqual(f.commands[0], { phase_token: f.mock.state.phase_token });
    assert.equal(await f.page.locator('#resume').isDisabled(), true);
    await f.phase('resuming', { resume_available: false, job: { action: 'internal_sync', state: 'running', timeout_s: null } });
    assert.match(await f.page.locator('#instruction').innerText(), /checking the loaded runtime/);
    assert.equal(await f.page.locator('#confirm').isVisible(), false);
    await f.phase('next', { session_id: 'b'.repeat(32) });
    assert.equal(await f.page.locator('#confirm').innerText(), 'Prepare dry swing');
    assert.equal(await f.page.locator('#confirm').isEnabled(), true);
    assert.equal(f.commands.length, 1);
    await f.phase('stopped', { resume_available: false, resume_reason: 'runtime identity could not be verified', job: { action: 'internal_sync', state: 'stopped', timeout_s: null } });
    assert.equal(await f.page.locator('#resume').isVisible(), false);
    assert.match(await f.page.locator('#message').innerText(), /runtime identity could not be verified/);
    await f.page.reload();
    await f.page.waitForFunction(() => document.querySelector('#connection').textContent === 'Status connected');
    assert.equal(await f.page.locator('#resume').isVisible(), false);
    assert.equal(f.commands.length, 1);
    assert.deepEqual(f.errors, []);
  } finally { await f.context.close(); }
});

test('mouse and touch drags scroll without opening links; touchscreen tap opens one raw file', async () => {
  const f = await fixture();
  try {
    await f.phase('next', { attempts: Array.from({ length: 15 }, (_, i) => saved(i + 1)) });
    await f.page.locator('#attempts').scrollIntoViewIfNeeded();
    const list = f.page.locator('#attempts');
    const box = await list.boundingBox();
    await f.page.mouse.move(box.x + 40, box.y + 180);
    await f.page.mouse.down();
    await f.page.mouse.move(box.x + 40, box.y + 20, { steps: 12 });
    await f.page.mouse.up();
    assert.ok(await list.evaluate(el => el.scrollTop) > 0);
    assert.equal(f.files.length, 0);
    await list.evaluate(el => { el.scrollTop = 0; });
    const download = f.page.waitForEvent('download');
    await f.page.locator('.attempt').first().getByRole('link', { name: 'Attempt record', exact: true }).tap();
    const record = await download;
    assert.equal(record.suggestedFilename(), 'RESULT.json');
    assert.equal(fs.readFileSync(await record.path(), 'utf8'), '{}');
    assert.equal(f.page.url(), origin + '/internal-sync.html');
    assert.equal(f.files.length, 1);
    await f.page.goto(origin + '/internal-sync.html');
    await f.page.locator('#attempts').scrollIntoViewIfNeeded();
    const touchBox = await list.boundingBox();
    const cdp = await f.context.newCDPSession(f.page);
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: touchBox.x + 40, y: touchBox.y + 180 }] });
    for (let y = 170; y >= 20; y -= 15) {
      await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: touchBox.x + 40, y: touchBox.y + y }] });
      await f.page.waitForTimeout(30);
    }
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
    assert.ok(await list.evaluate(el => el.scrollTop) > 0);
    assert.equal(f.files.length, 1);
    await f.page.waitForFunction(() => {
      const top = document.querySelector('#attempts').scrollTop;
      if (!window.scrollSettled || window.scrollSettled.top !== top) window.scrollSettled = { top, at: Date.now() };
      return Date.now() - window.scrollSettled.at > 300;
    });
    await list.evaluate(el => { el.scrollTop = 0; });
    const afterDrag = f.page.waitForEvent('download');
    await f.page.locator('.attempt').first().getByRole('link', { name: 'Attempt record', exact: true }).tap();
    assert.equal(fs.readFileSync(await (await afterDrag).path(), 'utf8'), '{}');
    assert.equal(f.files.length, 2);
    assert.equal(f.page.url(), origin + '/internal-sync.html');
    assert.deepEqual(f.errors, []);
  } finally { await f.context.close(); }
});
