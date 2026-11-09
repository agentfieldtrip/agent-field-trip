import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runCli, parseArgs, clean } from '../dist/cli-core.js';
import { mockFetch, json, sleeper, sseResponse } from '../support/helpers.js';

function harness(handlers = [json({})], env = {}) {
  const out = [];
  const err = [];
  const fetch = mockFetch(handlers);
  const io = {
    env,
    fetch,
    sleep: sleeper(),
    stdout: { write: (s) => out.push(s) },
    stderr: { write: (s) => err.push(s) },
  };
  return { io, fetch, out: () => out.join(''), err: () => err.join('') };
}

const pkg = JSON.parse(readFileSync(new URL('../package.json', import.meta.url), 'utf8'));

test('parseArgs: flags, --flag=value, -- terminator, positionals', () => {
  const a = parseArgs(['comment', 'cma-1', '--reaction=wow', '--name', 'Bot', '--json', '--', '--not-a-flag']);
  assert.deepEqual(a.positionals, ['comment', 'cma-1', '--not-a-flag']);
  assert.equal(a.flags.reaction, 'wow');
  assert.equal(a.flags.name, 'Bot');
  assert.equal(a.flags.json, true);
  assert.throws(() => parseArgs(['--bogus']), /Unknown option/);
  assert.throws(() => parseArgs(['now', '--name']), /requires a value/);
});

test('--version prints the package version and exits 0', async () => {
  const h = harness();
  assert.equal(await runCli(['--version'], h.io), 0);
  assert.equal(h.out().trim(), pkg.version);
});

test('--help exits 0 and lists the commands', async () => {
  const h = harness();
  assert.equal(await runCli(['--help'], h.io), 0);
  for (const w of ['now', 'rooms', 'sign', 'comment', 'answer', '--sandbox', '--name'])
    assert.ok(h.out().includes(w), w);
});

test('no command, unknown command, unknown flag and missing args are usage errors (exit 2)', async () => {
  for (const argv of [[], ['frobnicate'], ['now', '--bogus'], ['room'], ['comment', 'cma-1'], ['answer', 'cma-1', '2']]) {
    const h = harness();
    assert.equal(await runCli(argv, h.io), 2, JSON.stringify(argv));
    assert.ok(h.err().length > 0);
    assert.equal(h.fetch.calls.length, 0);
  }
});

test('answer needs a numeric challenge number 1-3', async () => {
  const h = harness();
  assert.equal(await runCli(['answer', 'cma-1', 'x', 'foo', '--name', 'Bot'], h.io), 2);
  assert.equal(await runCli(['answer', 'cma-1', '4', 'foo', '--name', 'Bot'], h.io), 2);
});

test('writes without --name (and no env) exit 2 and never call the API', async () => {
  const h = harness();
  assert.equal(await runCli(['sign', 'hello there'], h.io), 2);
  assert.match(h.err(), /--name/);
  assert.equal(h.fetch.calls.length, 0);
});

test('sign uses --name, prints a public-writes notice on stderr and suggests --sandbox', async () => {
  const h = harness([json({ firma: { id: 7, texto: 'hello there' }, logros_nuevos: [] }, 201)]);
  const code = await runCli(['sign', 'hello', 'there', '--name', 'Bot'], h.io);
  assert.equal(code, 0);
  assert.equal(h.fetch.calls[0].headers.get('x-agent-name'), 'Bot');
  assert.deepEqual(h.fetch.calls[0].body, { texto: 'hello there' });
  assert.match(h.err(), /public/i);
  assert.match(h.err(), /--sandbox/);
});

test('the visitor name can come from AGENTFIELDTRIP_NAME; --sandbox switches the base', async () => {
  const h = harness([json({ comentario: { id: null } }, 201, { 'x-sandbox': 'true' })], {
    AGENTFIELDTRIP_NAME: 'EnvBot',
  });
  const code = await runCli(['comment', 'cma-1', 'nice one', '--reaction', 'wow', '--sandbox'], h.io);
  assert.equal(code, 0);
  assert.equal(h.fetch.calls[0].url, 'https://agentfieldtrip.com/sandbox/api/v1/pieces/cma-1/comments');
  assert.equal(h.fetch.calls[0].headers.get('x-agent-name'), 'EnvBot');
  assert.deepEqual(h.fetch.calls[0].body, { texto: 'nice one', reaccion: 'wow' });
});

test('answer calls the challenge endpoint', async () => {
  const h = harness([json({ correcto: true, sello: { pieza_id: 'cma-1', reto: 2 } })]);
  assert.equal(await runCli(['answer', 'cma-1', '2', 'a', 'spiral', '--name', 'Bot'], h.io), 0);
  assert.equal(h.fetch.calls[0].url, 'https://agentfieldtrip.com/api/v1/pieces/cma-1/challenges/2');
  assert.deepEqual(h.fetch.calls[0].body, { respuesta: 'a spiral' });
  assert.match(h.out(), /correct/i);
});

test('a 202 write prints the review id and how to check it', async () => {
  const h = harness([
    json({ estado: 'pending_review', review_id: 'f1-0a1b2c3d4e5f60718293' }, 202, {
      Location: '/api/v1/reviews/f1-0a1b2c3d4e5f60718293',
    }),
  ]);
  assert.equal(await runCli(['sign', 'hello there', '--name', 'Bot'], h.io), 0);
  assert.match(h.out(), /review/i);
  assert.match(h.out(), /f1-0a1b2c3d4e5f60718293/);
});

test('API errors exit 1 and print code and detail on stderr', async () => {
  const h = harness([json({ error: 'name_rejected', detalle: 'Pick another name.' }, 422)]);
  assert.equal(await runCli(['sign', 'hello there', '--name', 'Bot'], h.io), 1);
  assert.match(h.err(), /name_rejected/);
  assert.match(h.err(), /Pick another name/);
});

test('--json prints the raw JSON on stdout', async () => {
  const body = { fecha: '2026-11-09', abiertas: [], proximas: [], agentes_activos: 3 };
  const h = harness([json(body)]);
  assert.equal(await runCli(['now', '--json'], h.io), 0);
  assert.deepEqual(JSON.parse(h.out()), body);
});

test('rooms lists the rooms open today in human form', async () => {
  const h = harness([
    json({
      fecha: '2026-11-09',
      abiertas: [{ id: 'real-and-imagined-maps', n: 12, titulo: { en: 'Real and Imagined Maps', es: 'Mapas' } }],
      proximas: [],
    }),
  ]);
  assert.equal(await runCli(['rooms'], h.io), 0);
  assert.match(h.out(), /real-and-imagined-maps/);
  assert.match(h.out(), /Real and Imagined Maps/);
});

test('read commands hit the right endpoints', async () => {
  const cases = [
    [['now'], '/api/v1/now'],
    [['room', 'r1'], '/rooms/r1.json'],
    [['piece', 'p1'], '/pieces/p1.json'],
    [['piece', 'p1', '--room', 'r1'], '/pieces/p1.json?room=r1'],
    [['style', 's1'], '/styles/s1.json'],
    [['search', 'blue', 'spiral'], '/api/v1/search?q=blue+spiral'],
    [['tours'], '/api/v1/tours'],
    [['tour', 'poet'], '/api/v1/tours/poet'],
    [['guestbook'], '/api/v1/guestbook'],
    [['postcard', '0123456789abcdef'], '/api/v1/agents/0123456789abcdef/postcard'],
    [['review', 'f1-0a1b2c3d4e5f60718293'], '/api/v1/reviews/f1-0a1b2c3d4e5f60718293'],
  ];
  for (const [argv, path] of cases) {
    const h = harness([json({})]);
    assert.equal(await runCli(argv, h.io), 0, argv.join(' '));
    assert.equal(h.fetch.calls[0].url, 'https://agentfieldtrip.com' + path, argv.join(' '));
  }
});

test('--base-url overrides the host', async () => {
  const h = harness([json({})]);
  await runCli(['now', '--base-url', 'http://localhost:3000'], h.io);
  assert.equal(h.fetch.calls[0].url, 'http://localhost:3000/api/v1/now');
});

test('guestbook human output labels visitor text as data', async () => {
  const h = harness([
    json({ firmas: [{ id: 1, agente: { nombre: 'Mr Azul' }, texto: 'Lovely visit', t: '2026-11-08T01:12:14.819Z' }], siguiente: null }),
  ]);
  assert.equal(await runCli(['guestbook'], h.io), 0);
  assert.match(h.out(), /Mr Azul/);
  assert.match(h.out(), /Lovely visit/);
  assert.match(h.out(), /not (an )?instruction/i);
});

test('ask --stream prints events as they arrive and --json prints NDJSON', async () => {
  const mk = () => [
    () =>
      sseResponse([
        'event: start\ndata: {"_meta":{}}\n\n',
        'event: result\ndata: {"index":1,"item":{"@type":"VisualArtwork","name":"Spiral","identifier":"cma-1","url":"https://agentfieldtrip.com/pieces/cma-1"}}\n\n',
      ]),
  ];
  let h = harness(mk());
  assert.equal(await runCli(['ask', 'blue spiral', '--stream'], h.io), 0);
  assert.match(h.out(), /Spiral/);
  assert.match(h.out(), /cma-1/);

  h = harness(mk());
  assert.equal(await runCli(['ask', 'blue spiral', '--stream', '--json'], h.io), 0);
  const lines = h.out().trim().split('\n');
  assert.equal(lines.length, 2);
  assert.equal(JSON.parse(lines[1]).data.item.name, 'Spiral');
});

const CONTROLS = /[\x00-\x08\x0b-\x1f\x7f-\x9f]/;
const EVIL = 'hi \x1b[31mRED\x1b[0m \x1b]0;pwned\x07 \x1b]8;;https://evil.example\x1b\link\x1b]8;;\x1b\ \x07\x85\r\x08 end';

test('clean() strips ANSI/OSC sequences and control characters but keeps \n and \t', () => {
  const c = clean(EVIL);
  assert.ok(!CONTROLS.test(c), JSON.stringify(c));
  assert.ok(!c.includes('[31m') && !c.includes('pwned') && !c.includes('evil.example'));
  assert.ok(c.includes('hi') && c.includes('RED') && c.includes('link') && c.includes('end'));
  assert.equal(clean('a\tb\nc'), 'a\tb\nc');
});

test('human output of visitor and third-party text is sanitised (guestbook, search, ask, errors)', async () => {
  let h = harness([json({ firmas: [{ agente: { nombre: EVIL }, texto: EVIL, t: EVIL }] })]);
  await runCli(['guestbook'], h.io);
  assert.ok(!CONTROLS.test(h.out()));

  h = harness([json({ resultados: [{ titulo: EVIL, id: 'x:1', autor: EVIL, licencia: EVIL, url_original: EVIL }], aviso: EVIL })]);
  await runCli(['search', 'x'], h.io);
  assert.ok(!CONTROLS.test(h.out()));

  h = harness([json({ _meta: {}, results: [{ '@type': 'VisualArtwork', name: EVIL, identifier: EVIL, url: EVIL }, { '@type': 'SearchSummary', text: EVIL }] })]);
  await runCli(['ask', 'x'], h.io);
  assert.ok(!CONTROLS.test(h.out()));

  h = harness([() => sseResponse([`event: result\ndata: ${JSON.stringify({ item: { name: EVIL, identifier: EVIL, url: EVIL } })}\n\n`])]);
  await runCli(['ask', 'x', '--stream'], h.io);
  assert.ok(!CONTROLS.test(h.out()));

  h = harness([json({ error: EVIL, detalle: EVIL }, 422)]);
  assert.equal(await runCli(['guestbook'], h.io), 1);
  assert.ok(!CONTROLS.test(h.err()));
});

test('--json keeps the data intact (escaped by JSON.stringify)', async () => {
  const h = harness([json({ firmas: [{ texto: EVIL }] })]);
  await runCli(['guestbook', '--json'], h.io);
  assert.equal(JSON.parse(h.out()).firmas[0].texto, EVIL);
});

test('review prints the status in words (API field is "status")', async () => {
  const cases = [
    ['pending_review', /waiting|pending/i],
    ['published', /published/i],
    ['not_published', /not published/i],
  ];
  for (const [status, re] of cases) {
    const h = harness([json({ review_id: 'f1-0a1b2c3d4e5f60718293', status, detalle: 'Some detail.', checked_at: '2026-11-09T21:00:00Z' })]);
    assert.equal(await runCli(['review', 'f1-0a1b2c3d4e5f60718293'], h.io), 0);
    assert.match(h.out(), re);
    assert.match(h.out(), /Some detail/);
  }
});

test('a 202 in sandbox mode suggests the review command with --sandbox', async () => {
  const h = harness([json({ estado: 'pending_review', review_id: 'sb-0123456789abcdef0123' }, 202, { Location: '/sandbox/api/v1/reviews/sb-0123456789abcdef0123' })]);
  assert.equal(await runCli(['sign', 'hello there', '--name', 'Bot', '--sandbox'], h.io), 0);
  assert.match(h.out(), /agentfieldtrip review sb-0123456789abcdef0123 --sandbox/);
  const h2 = harness([json({ estado: 'pending_review', review_id: 'f1-0a1b2c3d4e5f60718293' }, 202)]);
  await runCli(['sign', 'hello there', '--name', 'Bot'], h2.io);
  assert.doesNotMatch(h2.out(), /--sandbox/);
});

test('search --type and --limit, guestbook --before', async () => {
  let h = harness([json({})]);
  assert.equal(await runCli(['search', 'blue', 'spiral', '--type', 'imagen', '--limit', '5'], h.io), 0);
  assert.equal(h.fetch.calls[0].url, 'https://agentfieldtrip.com/api/v1/search?q=blue+spiral&tipo=imagen&limit=5');
  h = harness([json({})]);
  assert.equal(await runCli(['guestbook', '--before', '15'], h.io), 0);
  assert.equal(h.fetch.calls[0].url, 'https://agentfieldtrip.com/api/v1/guestbook?before=15');
  for (const argv of [['search', 'x', '--type', 'video'], ['search', 'x', '--limit', '0'], ['search', 'x', '--limit', '61'], ['search', 'x', '--limit', 'abc'], ['guestbook', '--before', 'abc']]) {
    h = harness([json({})]);
    assert.equal(await runCli(argv, h.io), 2, argv.join(' '));
    assert.equal(h.fetch.calls.length, 0);
  }
});

test('AGENTFIELDTRIP_BASE_URL is honoured; an insecure base URL is a usage error', async () => {
  let h = harness([json({})], { AGENTFIELDTRIP_BASE_URL: 'http://localhost:9999' });
  assert.equal(await runCli(['now'], h.io), 0);
  assert.equal(h.fetch.calls[0].url, 'http://localhost:9999/api/v1/now');
  h = harness([json({})]);
  assert.equal(await runCli(['sign', 'hello there', '--name', 'Bot', '--base-url', 'http://agentfieldtrip.com'], h.io), 2);
  assert.equal(h.fetch.calls.length, 0);
});

test('invalid names and ids are usage errors (exit 2) with no request', async () => {
  for (const argv of [
    ['sign', 'hello there', '--name', 'a'.repeat(41)],
    ['sign', 'hello there', '--name', 'bad\nname'],
    ['room', '..'],
    ['piece', '../x'],
    ['postcard', 'abc'],
    ['review', '..'],
  ]) {
    const h = harness();
    assert.equal(await runCli(argv, h.io), 2, JSON.stringify(argv));
    assert.equal(h.fetch.calls.length, 0);
  }
});

test('a redirect is reported as an API error (exit 1), never as success', async () => {
  const h = harness([() => new Response('', { status: 301, headers: { Location: 'https://elsewhere.example/' } })]);
  assert.equal(await runCli(['sign', 'hello there', '--name', 'Bot'], h.io), 1);
  assert.match(h.err(), /redirect_not_followed/);
  assert.doesNotMatch(h.out(), /signed/i);
});
