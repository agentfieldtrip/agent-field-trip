import test from 'node:test';
import assert from 'node:assert/strict';
import { parseSse, AgentFieldTripError } from '../dist/index.js';
import { sseResponse } from '../support/helpers.js';

async function collect(chunks) {
  const out = [];
  for await (const ev of parseSse(sseResponse(chunks).body)) out.push(ev);
  return out;
}

test('parses events split across arbitrary chunk boundaries', async () => {
  const out = await collect(['event: a\nda', 'ta: {"x":1}\n', '\nevent: b\ndata: {"y":2}\n\n']);
  assert.deepEqual(out, [
    { event: 'a', data: { x: 1 } },
    { event: 'b', data: { y: 2 } },
  ]);
});

test('handles CRLF, comments, multi-line data and a missing event name', async () => {
  const out = await collect([': keep-alive\r\n\r\ndata: line1\r\ndata: line2\r\n\r\n']);
  assert.deepEqual(out, [{ event: 'message', data: 'line1\nline2' }]);
});

test('non-JSON data stays a string and a final event without a blank line is flushed', async () => {
  const out = await collect(['event: text\ndata: hello']);
  assert.deepEqual(out, [{ event: 'text', data: 'hello' }]);
});

test('multi-byte characters split across chunks decode correctly', async () => {
  const bytes = new TextEncoder().encode('event: m\ndata: "café ☃"\n\n');
  const half = Math.floor(bytes.length / 2);
  const stream = new ReadableStream({
    start(c) {
      c.enqueue(bytes.slice(0, half));
      c.enqueue(bytes.slice(half));
      c.close();
    },
  });
  const out = [];
  for await (const ev of parseSse(stream)) out.push(ev);
  assert.deepEqual(out, [{ event: 'm', data: 'café ☃' }]);
});

test('a lone CR is a line end (also when CR and LF arrive in different chunks)', async () => {
  const out = await collect(['event: a\rdata: {"x":1}\r\r', 'event: b\r', '\ndata: two\r\n\r\n']);
  assert.deepEqual(out, [
    { event: 'a', data: { x: 1 } },
    { event: 'b', data: 'two' },
  ]);
});

test('an event larger than maxEventBytes fails with response_too_large', async () => {
  const big = 'data: ' + 'x'.repeat(500);
  const run = async () => {
    for await (const _ of parseSse(sseResponse([big.slice(0, 250), big.slice(250)]).body, { maxEventBytes: 300 })) void _;
  };
  await assert.rejects(run(), (e) => e instanceof AgentFieldTripError && e.code === 'response_too_large');
});

test('a stalled stream fails with timeout after idleTimeoutMs', async () => {
  const stream = new ReadableStream({
    start(c) {
      c.enqueue(new TextEncoder().encode('event: a\ndata: 1\n\n'));
    },
  });
  const seen = [];
  const run = async () => {
    for await (const ev of parseSse(stream, { idleTimeoutMs: 30 })) seen.push(ev);
  };
  await assert.rejects(run(), (e) => e instanceof AgentFieldTripError && e.code === 'timeout');
  assert.equal(seen.length, 1);
});
