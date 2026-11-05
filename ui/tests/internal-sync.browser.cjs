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

async function fixture(viewport = { width: 800, height: 400 }) {
  const context = await browser.newContext({ viewport, hasTouch: true });
  const page = await context.newPage();
  const errors = [], commands = [], files = [];
  currentDownloads = files;
  page.on('pageerror', error => errors.push(String(error)));
  const mock = {
    state: { configured: true, session_id: 'a'.repeat(32), phase: 'next', phase_token: 'next-0', attempts: [], job: { action: 'internal_sync', state: 'running', started_at: new Date().toISOString() } },
    network: 'ok', advance: false,
  };
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    assert.equal(url.origin, origin);
    if (url.pathname === '/internal-sync.html') return route.fulfill({ contentType: 'text/html', body: html });
    if (url.pathname.includes('/file/')) { files.push(url.pathname); return route.fulfill({ contentType: 'application/json', body: '{}' }); }
    if (route.request().method() === 'POST') {
      const payload = route.request().postDataJSON();
      commands.push(payload);
      assert.equal(payload.phase_token, mock.state.phase_token);
      if (mock.advance) {
        const phase = { switches_set: 'reset', reset_pressed: 'next', stop: 'stopped', restored: 'stopped' }[payload.command];
        if (phase) mock.state = { ...mock.state, phase, phase_token: phase + commands.length, resources_released: true };
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
  await page.waitForFunction(() => document.querySelector('#connection').textContent === 'Status connected');
  async function phase(name, extra = {}) {
    mock.state = { ...mock.state, phase: name, phase_token: name + Math.random(), ...extra };
    await page.evaluate(() => refresh());
    await page.waitForFunction(name => document.querySelector('#phase').textContent !== 'Loading status…', name);
  }
  return { context, page, mock, commands, files, errors, phase };
}

function saved(index, status = 'captured_unqualified') {
  return { name: `attempt-${String(index).padStart(3, '0')}`, status, error: status === 'partial' ? 'TimeoutError: no IWR detection' : null, iwr_shot_id: index, camera: { clips: [{ metadata: 'camera/capture/metadata.json' }] } };
}

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
        await phase('armed');
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
    await f.page.locator('#stop').tap();
    await f.page.waitForFunction(() => document.querySelector('#phase').textContent === 'Stopped');
    assert.match(await f.page.locator('#message').innerText(), /new RAM load unsupervised/);
    await f.page.locator('#restored').tap();
    assert.deepEqual(f.commands.map(row => row.command), ['switches_set', 'reset_pressed', 'stop', 'restored']);
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
