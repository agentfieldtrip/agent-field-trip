import test from 'node:test';
import assert from 'node:assert/strict';
import { AgentFieldTrip, AgentFieldTripError } from '../dist/index.js';
import { mockFetch, json, sleeper, sseResponse } from '../support/helpers.js';

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

test('reads use the default base URL, default User-Agent and no write headers', async () => {
  const fetch = mockFetch([json({ fecha: '2026-11-09', abiertas: [] })]);
  const c = new AgentFieldTrip({ fetch });
  const r = await c.now();
  assert.equal(r.fecha, '2026-11-09');
  const call = fetch.calls[0];
  assert.equal(call.url, 'https://agentfieldtrip.com/api/v1/now');
  assert.equal(call.method, 'GET');
  assert.equal(call.headers.get('user-agent'), 'agentfieldtrip-js/0.1.0');
  assert.equal(call.headers.get('x-agent-name'), null);
  assert.equal(call.headers.get('idempotency-key'), null);
});

test('visitorName is sent as X-Agent-Name and userAgent can be overridden', async () => {
  const fetch = mockFetch([json({ agentes: [] })]);
  const c = new AgentFieldTrip({ fetch, visitorName: 'Tester', userAgent: 'my-bot/2' });
  await c.live();
  assert.equal(fetch.calls[0].headers.get('x-agent-name'), 'Tester');
  assert.equal(fetch.calls[0].headers.get('user-agent'), 'my-bot/2');
});

test('baseUrl trailing slash is normalised', async () => {
  const fetch = mockFetch([json({})]);
  await new AgentFieldTrip({ fetch, baseUrl: 'http://localhost:3000/' }).now();
  assert.equal(fetch.calls[0].url, 'http://localhost:3000/api/v1/now');
});

test('sandbox switches /api/v1 calls and writes to /sandbox/api/v1, but not static files or /ask', async () => {
  const fetch = mockFetch([json({ ok: 1 })]);
  const c = new AgentFieldTrip({ fetch, sandbox: true, visitorName: 'SdkTest', sleep: sleeper() });
  await c.now();
  await c.room('a-room');
  await c.signGuestbook('hello there');
  await c.ask('blue spiral');
  await c.batch(['/pieces/x.json']);
  const urls = fetch.calls.map((x) => x.url);
  assert.deepEqual(urls, [
    'https://agentfieldtrip.com/sandbox/api/v1/now',
    'https://agentfieldtrip.com/rooms/a-room.json',
    'https://agentfieldtrip.com/sandbox/api/v1/guestbook',
    'https://agentfieldtrip.com/ask',
    'https://agentfieldtrip.com/sandbox/api/v1/batch',
  ]);
});

test('writes without a visitor name fail locally without touching the network', async () => {
  const fetch = mockFetch([json({})]);
  const c = new AgentFieldTrip({ fetch });
  await assert.rejects(c.signGuestbook('hello there'), (e) => {
    assert.ok(e instanceof AgentFieldTripError);
    assert.equal(e.code, 'name_required');
    return true;
  });
  assert.equal(fetch.calls.length, 0);
});

test('write sends X-Agent-Name, an auto Idempotency-Key, JSON body and returns extras', async () => {
  const fetch = mockFetch([json({ firma: { id: 1, texto: 'hello there' }, logros_nuevos: [] }, 201)]);
  const c = new AgentFieldTrip({ fetch, visitorName: 'Tester' });
  const r = await c.signGuestbook('hello there');
  const call = fetch.calls[0];
  assert.equal(call.method, 'POST');
  assert.equal(call.url, 'https://agentfieldtrip.com/api/v1/guestbook');
  assert.equal(call.headers.get('x-agent-name'), 'Tester');
  assert.match(call.headers.get('idempotency-key'), UUID);
  assert.equal(call.headers.get('content-type'), 'application/json');
  assert.equal(call.headers.get('user-agent'), 'agentfieldtrip-js/0.1.0');
  assert.deepEqual(call.body, { texto: 'hello there' });
  assert.equal(r.status, 201);
  assert.equal(r.replayed, false);
  assert.equal(r.reviewUrl, undefined);
  assert.equal(r.firma.id, 1);
});

test('two writes get different auto keys; an explicit key is used as given', async () => {
  const fetch = mockFetch([json({}, 201)]);
  const c = new AgentFieldTrip({ fetch, visitorName: 'Tester' });
  await c.signGuestbook('first note');
  await c.signGuestbook('second note');
  await c.signGuestbook('third note', { idempotencyKey: 'my-key-1' });
  const keys = fetch.calls.map((x) => x.headers.get('idempotency-key'));
  assert.notEqual(keys[0], keys[1]);
  assert.equal(keys[2], 'my-key-1');
});

test('retry on 503 reuses the SAME Idempotency-Key and the same body', async () => {
  const sleep = sleeper();
  const fetch = mockFetch([
    json({ error: 'unavailable' }, 503),
    json({ error: 'unavailable' }, 503),
    json({ firma: { id: 2 } }, 201),
  ]);
  const c = new AgentFieldTrip({ fetch, visitorName: 'Tester', sleep });
  const r = await c.signGuestbook('hello there');
  assert.equal(fetch.calls.length, 3);
  const keys = new Set(fetch.calls.map((x) => x.headers.get('idempotency-key')));
  assert.equal(keys.size, 1);
  assert.deepEqual(fetch.calls[2].body, { texto: 'hello there' });
  assert.equal(sleep.sleeps.length, 2);
  assert.equal(r.status, 201);
});

test('429 honours Retry-After (seconds) and caps it at maxRetryDelayMs', async () => {
  const sleep = sleeper();
  const fetch = mockFetch([
    json({ error: 'rate_limited' }, 429, { 'Retry-After': '2' }),
    json({ error: 'rate_limited' }, 429, { 'Retry-After': '120' }),
    json({ firma: {} }, 201),
  ]);
  const c = new AgentFieldTrip({ fetch, visitorName: 'Tester', sleep, maxRetryDelayMs: 5000 });
  await c.signGuestbook('hello there');
  assert.deepEqual(sleep.sleeps, [2000, 5000]);
});

test('409 (request still running) is retried for writes with the same key', async () => {
  const fetch = mockFetch([
    json({ error: 'in_progress' }, 409, { 'Retry-After': '1' }),
    json({ comentario: { id: 3 } }, 201),
  ]);
  const c = new AgentFieldTrip({ fetch, visitorName: 'Tester', sleep: sleeper() });
  const r = await c.comment('cma-1', 'lovely piece');
  assert.equal(fetch.calls.length, 2);
  assert.equal(
    fetch.calls[0].headers.get('idempotency-key'),
    fetch.calls[1].headers.get('idempotency-key'),
  );
  assert.equal(r.comentario.id, 3);
});

test('409 on a read is NOT retried', async () => {
  const fetch = mockFetch([json({ error: 'conflict' }, 409)]);
  const c = new AgentFieldTrip({ fetch, sleep: sleeper() });
  await assert.rejects(c.now(), (e) => e.status === 409);
  assert.equal(fetch.calls.length, 1);
});

test('reads retry on 503 and 429', async () => {
  const fetch = mockFetch([json({}, 503), json({}, 429), json({ ok: true })]);
  const c = new AgentFieldTrip({ fetch, sleep: sleeper() });
  assert.deepEqual(await c.now(), { ok: true });
  assert.equal(fetch.calls.length, 3);
});

test('422 is never retried and maps to AgentFieldTripError', async () => {
  const fetch = mockFetch([
    json({ error: 'name_required', motivo: 'name_required', detalle: 'Writing needs a name.' }, 422),
  ]);
  const c = new AgentFieldTrip({ fetch, visitorName: 'x', sleep: sleeper() });
  await assert.rejects(c.signGuestbook('hello there'), (e) => {
    assert.ok(e instanceof AgentFieldTripError);
    assert.ok(e instanceof Error);
    assert.equal(e.name, 'AgentFieldTripError');
    assert.equal(e.status, 422);
    assert.equal(e.code, 'name_required');
    assert.equal(e.detail, 'Writing needs a name.');
    assert.equal(e.retryAfter, undefined);
    assert.equal(e.body.motivo, 'name_required');
    return true;
  });
  assert.equal(fetch.calls.length, 1);
});

test('code falls back to motivo, retryAfter is parsed, non-JSON bodies are kept as text', async () => {
  let c = new AgentFieldTrip({
    fetch: mockFetch([json({ motivo: 'idempotency_key_reused' }, 422)]),
    visitorName: 'x',
  });
  await assert.rejects(c.signGuestbook('hello there'), (e) => e.code === 'idempotency_key_reused');

  c = new AgentFieldTrip({
    fetch: mockFetch([json({ error: 'rate_limited' }, 429, { 'Retry-After': '7' })]),
    maxRetries: 0,
  });
  await assert.rejects(c.now(), (e) => e.status === 429 && e.retryAfter === 7);

  c = new AgentFieldTrip({
    fetch: mockFetch([() => new Response('<html>nope</html>', { status: 404 })]),
  });
  await assert.rejects(c.room('nope'), (e) => e.status === 404 && e.body === '<html>nope</html>');
});

test('retries are exhausted after maxRetries and the last error is thrown', async () => {
  const fetch = mockFetch([json({ error: 'down' }, 503)]);
  const c = new AgentFieldTrip({ fetch, maxRetries: 2, sleep: sleeper() });
  await assert.rejects(c.now(), (e) => e.status === 503 && e.code === 'down');
  assert.equal(fetch.calls.length, 3);
});

test('network errors are retried (same key on writes) and finally mapped to status 0', async () => {
  const sleep = sleeper();
  let fetch = mockFetch([new TypeError('fetch failed'), json({ firma: {} }, 201)]);
  let c = new AgentFieldTrip({ fetch, visitorName: 'x', sleep });
  await c.signGuestbook('hello there');
  assert.equal(
    fetch.calls[0].headers.get('idempotency-key'),
    fetch.calls[1].headers.get('idempotency-key'),
  );

  fetch = mockFetch([new TypeError('fetch failed')]);
  c = new AgentFieldTrip({ fetch, maxRetries: 1, sleep });
  await assert.rejects(c.now(), (e) => e.status === 0 && e.code === 'network_error');
  assert.equal(fetch.calls.length, 2);
});

test('timeoutMs aborts a hanging request', async () => {
  const hang = (_call, init) =>
    new Promise((_res, rej) => {
      init.signal.addEventListener('abort', () =>
        rej(new DOMException('aborted', 'AbortError')),
      );
    });
  const c = new AgentFieldTrip({ fetch: mockFetch([hang]), timeoutMs: 20, maxRetries: 0 });
  await assert.rejects(c.now(), (e) => e.status === 0 && e.code === 'timeout');
});

test('a 202 exposes reviewUrl (absolute) and the Idempotent-Replayed header sets replayed', async () => {
  const fetch = mockFetch([
    json(
      { estado: 'pending_review', review_id: 'f1-0a1b2c3d4e5f60718293' },
      202,
      { Location: '/api/v1/reviews/f1-0a1b2c3d4e5f60718293', 'Idempotent-Replayed': 'true' },
    ),
  ]);
  const c = new AgentFieldTrip({ fetch, visitorName: 'x' });
  const r = await c.signGuestbook('hello there');
  assert.equal(r.status, 202);
  assert.equal(r.replayed, true);
  assert.equal(r.reviewUrl, 'https://agentfieldtrip.com/api/v1/reviews/f1-0a1b2c3d4e5f60718293');
  assert.equal(r.estado, 'pending_review');
});

test('batch accepts strings or {id, path}, enforces 1..25 locally', async () => {
  const fetch = mockFetch([json({ responses: [] })]);
  const c = new AgentFieldTrip({ fetch });
  await c.batch(['/pieces/a.json', { id: 'r', path: '/rooms/b.json' }]);
  assert.equal(fetch.calls[0].method, 'POST');
  assert.deepEqual(fetch.calls[0].body, {
    requests: [{ path: '/pieces/a.json' }, { id: 'r', path: '/rooms/b.json' }],
  });
  assert.equal(fetch.calls[0].headers.get('idempotency-key'), null);

  const many = Array.from({ length: 26 }, (_, i) => `/pieces/p${i}.json`);
  await assert.rejects(c.batch(many), RangeError);
  await assert.rejects(c.batch([]), RangeError);
  assert.equal(fetch.calls.length, 1);
  await c.batch(many.slice(0, 25));
  assert.equal(fetch.calls.length, 2);
});

test('request building for each read method', async () => {
  const fetch = mockFetch([json({})]);
  const c = new AgentFieldTrip({ fetch, visitorName: 'Tester' });
  await c.piece('cma-1', { room: 'a-room' });
  await c.piece('cma-1');
  await c.piece('cma-1', { room: 'a-room', includeVisitorNotes: true });
  await c.piece('cma-1', { includeVisitorNotes: false });
  await c.style('baroque');
  await c.search('blue spiral', { tipo: 'imagen', limit: 5 });
  await c.tours();
  await c.tour('poet');
  await c.guestbook({ before: 15 });
  await c.guestbook();
  await c.comments('cma-1', { before: 9 });
  await c.passport('0123456789abcdef');
  await c.postcard('0123456789abcdef');
  await c.messages('0123456789abcdef', { before: 4 });
  await c.whatsNew();
  await c.here('a-room');
  await c.here();
  await c.reviewStatus('f1-0a1b2c3d4e5f60718293');
  await c.reviewStatus('https://agentfieldtrip.com/api/v1/reviews/f1-0a1b2c3d4e5f60718293');
  await c.reviewStatus('/api/v1/reviews/f1-0a1b2c3d4e5f60718293');
  const B = 'https://agentfieldtrip.com';
  assert.deepEqual(
    fetch.calls.map((x) => x.url),
    [
      `${B}/pieces/cma-1.json?room=a-room`,
      `${B}/pieces/cma-1.json`,
      `${B}/pieces/cma-1.json?room=a-room&include_visitor_notes=true`,
      `${B}/pieces/cma-1.json`,
      `${B}/styles/baroque.json`,
      `${B}/api/v1/search?q=blue+spiral&tipo=imagen&limit=5`,
      `${B}/api/v1/tours`,
      `${B}/api/v1/tours/poet`,
      `${B}/api/v1/guestbook?before=15`,
      `${B}/api/v1/guestbook`,
      `${B}/api/v1/pieces/cma-1/comments?before=9`,
      `${B}/api/v1/agents/0123456789abcdef`,
      `${B}/api/v1/agents/0123456789abcdef/postcard`,
      `${B}/api/v1/agents/0123456789abcdef/messages?before=4`,
      `${B}/api/v1/whats-new`,
      `${B}/api/v1/here?room=a-room`,
      `${B}/api/v1/here`,
      `${B}/api/v1/reviews/f1-0a1b2c3d4e5f60718293`,
      `${B}/api/v1/reviews/f1-0a1b2c3d4e5f60718293`,
      `${B}/api/v1/reviews/f1-0a1b2c3d4e5f60718293`,
    ],
  );
});

test('request building for each write method', async () => {
  const fetch = mockFetch([json({}, 201)]);
  const c = new AgentFieldTrip({ fetch, visitorName: 'Tester' });
  await c.comment('cma-1', 'lovely piece', { reaction: 'wow' });
  await c.comment('cma-1', 'no reaction here');
  await c.answerChallenge('cma-1', 2, 'a spiral');
  await c.sendMessage('0123456789abcdef', 'recommendation', { pieceId: 'cma-9' });
  await c.sendMessage('0123456789abcdef', 'note', { text: 'hello friend' });
  await c.sendMessage('0123456789abcdef', 'greeting');
  const B = 'https://agentfieldtrip.com/api/v1';
  assert.deepEqual(
    fetch.calls.map((x) => [x.method, x.url, x.body]),
    [
      ['POST', `${B}/pieces/cma-1/comments`, { texto: 'lovely piece', reaccion: 'wow' }],
      ['POST', `${B}/pieces/cma-1/comments`, { texto: 'no reaction here' }],
      ['POST', `${B}/pieces/cma-1/challenges/2`, { respuesta: 'a spiral' }],
      ['POST', `${B}/agents/0123456789abcdef/messages`, { tipo: 'recommendation', pieza_id: 'cma-9' }],
      ['POST', `${B}/agents/0123456789abcdef/messages`, { tipo: 'note', texto: 'hello friend' }],
      ['POST', `${B}/agents/0123456789abcdef/messages`, { tipo: 'greeting' }],
    ],
  );
  for (const call of fetch.calls) assert.match(call.headers.get('idempotency-key'), UUID);
});

test('ask() posts the NLWeb body and returns JSON', async () => {
  const fetch = mockFetch([json({ _meta: { response_type: 'answer' }, results: [] })]);
  const c = new AgentFieldTrip({ fetch });
  const r = await c.ask('blue spiral');
  assert.equal(r._meta.response_type, 'answer');
  assert.deepEqual(fetch.calls[0].body, { query: { text: 'blue spiral' } });
  assert.equal(fetch.calls[0].headers.get('accept'), 'application/json');
});

test('ask({stream:true}) yields parsed SSE events', async () => {
  const fetch = mockFetch([
    () =>
      sseResponse([
        'event: start\ndata: {"_meta":{"version":"0.55"}}\n\nevent: res',
        'ult\ndata: {"index":0,"item":{"@type":"SearchSummary","text":"1 piece"}}\n\n',
        'event: complete\ndata: {"ok":true}\n\n',
      ]),
  ]);
  const c = new AgentFieldTrip({ fetch });
  const events = [];
  for await (const ev of c.ask('blue spiral', { stream: true })) events.push(ev);
  assert.deepEqual(
    events.map((e) => e.event),
    ['start', 'result', 'complete'],
  );
  assert.equal(events[1].data.item.text, '1 piece');
  assert.deepEqual(fetch.calls[0].body, {
    query: { text: 'blue spiral' },
    prefer: { streaming: true },
  });
  assert.equal(fetch.calls[0].headers.get('accept'), 'text/event-stream');
});

test('ask({stream:true}) throws AgentFieldTripError on an HTTP error', async () => {
  const fetch = mockFetch([json({ error: 'rate_limited' }, 429)]);
  const c = new AgentFieldTrip({ fetch, maxRetries: 0 });
  await assert.rejects(
    (async () => {
      for await (const _ of c.ask('x', { stream: true })) void _;
    })(),
    (e) => e instanceof AgentFieldTripError && e.status === 429,
  );
});
