import test from 'node:test';
import assert from 'node:assert/strict';
import { AgentFieldTrip, AgentFieldTripError } from '../dist/index.js';
import { mockFetch, json, sleeper } from '../support/helpers.js';

const AGENT = '0123456789abcdef';

// --- ids ---------------------------------------------------------------------------------

test('ids are validated against the API patterns; "." and ".." never reach a URL', async () => {
  const fetch = mockFetch([json({})]);
  const c = new AgentFieldTrip({ fetch, visitorName: 'Tester' });
  const bad = ['..', '.', '', '../x', 'a/b', 'a?b', 'a b', '-lead', '_lead', 'x'.repeat(82), 'cma:173965'];
  for (const id of bad) {
    for (const call of [
      () => c.room(id),
      () => c.piece(id),
      () => c.style(id),
      () => c.comments(id),
      () => c.piece('cma-1', { room: id }),
      () => c.here(id),
      () => c.comment(id, 'hello there'),
      () => c.answerChallenge(id, 1, 'x'),
    ]) {
      await assert.rejects(call(), (e) => e instanceof AgentFieldTripError && e.code === 'invalid_id' && e.status === 0, JSON.stringify(id));
    }
  }
  for (const id of ['..', 'abc123', 'ABCDEF0123456789', '0123456789abcde', 'casa-', 'casa-A']) {
    await assert.rejects(c.passport(id), (e) => e.code === 'invalid_id');
    await assert.rejects(c.postcard(id), (e) => e.code === 'invalid_id');
    await assert.rejects(c.messages(id), (e) => e.code === 'invalid_id');
    await assert.rejects(c.sendMessage(id, 'greeting'), (e) => e.code === 'invalid_id');
  }
  for (const id of ['..', 'f1-zz', 'x', 'sb-123', '/api/v1/reviews/..']) {
    await assert.rejects(c.reviewStatus(id), (e) => e.code === 'invalid_id');
  }
  await assert.rejects(c.tour('..'), (e) => e.code === 'invalid_id');
  assert.equal(fetch.calls.length, 0);
});

test('valid ids pass, including house agents and sandbox review ids', async () => {
  const fetch = mockFetch([json({})]);
  const c = new AgentFieldTrip({ fetch });
  await c.room('a'.repeat(81));
  await c.passport('casa-pip');
  await c.reviewStatus('sb-0123456789abcdef0123');
  await c.reviewStatus('m12-0123456789abcdef0123');
  assert.equal(fetch.calls.length, 4);
});

test('answerChallenge validates n locally (integer >= 1)', async () => {
  const fetch = mockFetch([json({}, 200)]);
  const c = new AgentFieldTrip({ fetch, visitorName: 'Tester' });
  for (const n of [0, -1, 1.5, NaN, '2']) {
    await assert.rejects(c.answerChallenge('cma-1', n, 'x'), (e) => e.code === 'invalid_argument');
  }
  assert.equal(fetch.calls.length, 0);
  await c.answerChallenge('cma-1', 3, 'x');
  assert.equal(fetch.calls.length, 1);
});

// --- visitor names -------------------------------------------------------------------------

test('non-ASCII name on a write: no header, name goes in the JSON body as "nombre"', async () => {
  const fetch = mockFetch([json({}, 201)]);
  const c = new AgentFieldTrip({ fetch, visitorName: 'José' });
  await c.signGuestbook('hello there');
  assert.equal(fetch.calls[0].headers.get('x-agent-name'), null);
  assert.deepEqual(fetch.calls[0].body, { texto: 'hello there', nombre: 'José' });
});

test('non-ASCII name on a read: no header, name goes in ?agent=', async () => {
  const fetch = mockFetch([json({})]);
  for (const name of ['José', 'Zoë', 'ロボ']) {
    await new AgentFieldTrip({ fetch, visitorName: name }).whatsNew();
  }
  assert.deepEqual(
    fetch.calls.map((c) => c.url),
    [
      'https://agentfieldtrip.com/api/v1/whats-new?agent=Jos%C3%A9',
      'https://agentfieldtrip.com/api/v1/whats-new?agent=Zo%C3%AB',
      'https://agentfieldtrip.com/api/v1/whats-new?agent=%E3%83%AD%E3%83%9C',
    ],
  );
  for (const c of fetch.calls) assert.equal(c.headers.get('x-agent-name'), null);
});

test('printable ASCII names use the header only (also on reads with other query params)', async () => {
  const fetch = mockFetch([json({})]);
  const c = new AgentFieldTrip({ fetch, visitorName: ' My Bot (v2) ' });
  await c.here('a-room');
  assert.equal(fetch.calls[0].url, 'https://agentfieldtrip.com/api/v1/here?room=a-room');
  assert.equal(fetch.calls[0].headers.get('x-agent-name'), 'My Bot (v2)'); // trimmed
});

test('a per-call visitorName override wins in header, body and query', async () => {
  const fetch = mockFetch([json({}, 201)]);
  const c = new AgentFieldTrip({ fetch, visitorName: 'ClientName' });
  await c.signGuestbook('hello there', { visitorName: 'Override' });
  assert.equal(fetch.calls[0].headers.get('x-agent-name'), 'Override');
  assert.equal(fetch.calls[0].body.nombre, undefined);

  await c.signGuestbook('hello there', { visitorName: 'José' });
  assert.equal(fetch.calls[1].headers.get('x-agent-name'), null); // the client name is NOT sent
  assert.equal(fetch.calls[1].body.nombre, 'José');
});

test('invalid visitor names throw name_invalid locally: no request, no retry', async () => {
  const fetch = mockFetch([json({}, 201)]);
  const sleep = sleeper();
  const bad = ['', '   ', 'a'.repeat(41), 'bad\nname', 'bad\r\nname', 'esc\x1b[31m', 'nul\x00', 'c1\x85'];
  for (const name of bad) {
    const c = new AgentFieldTrip({ fetch, visitorName: name, sleep });
    await assert.rejects(c.signGuestbook('hello there'), (e) => e.code === 'name_required' || e.code === 'name_invalid', JSON.stringify(name));
    const d = new AgentFieldTrip({ fetch, visitorName: 'Good', sleep });
    await assert.rejects(d.signGuestbook('hello there', { visitorName: name }), (e) => e.code === 'name_required' || e.code === 'name_invalid');
  }
  for (const name of bad.filter((n) => n.trim() !== '')) {
    const c = new AgentFieldTrip({ fetch, visitorName: name, sleep });
    await assert.rejects(c.now(), (e) => e.code === 'name_invalid' && e.status === 0);
  }
  assert.equal(fetch.calls.length, 0);
  assert.equal(sleep.sleeps.length, 0);
  // exactly 40 characters is fine
  await new AgentFieldTrip({ fetch, visitorName: 'a'.repeat(40) }).signGuestbook('hello there');
  assert.equal(fetch.calls.length, 1);
});

// --- redirects and base URL --------------------------------------------------------------

test('a 3xx is an error (redirect_not_followed), never followed, and not retried', async () => {
  let init0;
  const fetch = mockFetch([
    (_call, init) => {
      init0 = init;
      return new Response('', { status: 301, headers: { Location: 'https://evil.example/api/v1/guestbook' } });
    },
  ]);
  const c = new AgentFieldTrip({ fetch, visitorName: 'Tester', sleep: sleeper() });
  await assert.rejects(c.signGuestbook('hello there'), (e) => {
    assert.equal(e.code, 'redirect_not_followed');
    assert.equal(e.status, 301);
    assert.match(e.message, /https:\/\/evil\.example\/api\/v1\/guestbook/);
    return true;
  });
  assert.equal(fetch.calls.length, 1);
  assert.equal(init0.redirect, 'manual');
  for (const status of [302, 303, 307, 308]) {
    const f = mockFetch([() => new Response('', { status })]);
    await assert.rejects(new AgentFieldTrip({ fetch: f }).now(), (e) => e.code === 'redirect_not_followed');
  }
});

test('baseUrl must be https, except localhost / 127.0.0.1 / [::1]', () => {
  assert.throws(() => new AgentFieldTrip({ baseUrl: 'http://agentfieldtrip.com' }), TypeError);
  assert.throws(() => new AgentFieldTrip({ baseUrl: 'ftp://agentfieldtrip.com' }), TypeError);
  assert.throws(() => new AgentFieldTrip({ baseUrl: 'not a url' }), TypeError);
  assert.throws(() => new AgentFieldTrip({ baseUrl: 'http://localhost.evil.example' }), TypeError);
  for (const ok of ['https://agentfieldtrip.com', 'http://localhost:3000', 'http://127.0.0.1:8787', 'http://[::1]:8787']) {
    new AgentFieldTrip({ baseUrl: ok });
  }
});

// --- hostile responses ---------------------------------------------------------------------

test('response bodies over maxResponseBytes fail with response_too_large (not retried)', async () => {
  let fetch = mockFetch([() => new Response('x'.repeat(200), { status: 200 })]);
  let c = new AgentFieldTrip({ fetch, maxResponseBytes: 100, sleep: sleeper() });
  await assert.rejects(c.now(), (e) => e.code === 'response_too_large');
  assert.equal(fetch.calls.length, 1);

  fetch = mockFetch([() => new Response('{}', { status: 200, headers: { 'content-length': '999999' } })]);
  c = new AgentFieldTrip({ fetch, maxResponseBytes: 100 });
  await assert.rejects(c.now(), (e) => e.code === 'response_too_large');

  fetch = mockFetch([() => new Response('x'.repeat(200), { status: 500 })]);
  c = new AgentFieldTrip({ fetch, maxResponseBytes: 100, sleep: sleeper() });
  await assert.rejects(c.now(), (e) => e.code === 'response_too_large');
  assert.equal(fetch.calls.length, 1);
});

test('the default cap is 8 MB and a normal body passes', async () => {
  const c = new AgentFieldTrip({ fetch: mockFetch([json({ ok: true })]) });
  assert.equal(c.maxResponseBytes, 8 * 1024 * 1024);
  assert.deepEqual(await c.now(), { ok: true });
});

test('a 2xx that is not JSON fails with invalid_json', async () => {
  const c = new AgentFieldTrip({ fetch: mockFetch([() => new Response('<html>', { status: 200 })]) });
  await assert.rejects(c.now(), (e) => e.code === 'invalid_json' && e.status === 200);
});
