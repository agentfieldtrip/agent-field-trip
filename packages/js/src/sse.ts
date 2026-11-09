import { AgentFieldTripError } from './errors.js';
import type { SseEvent } from './types.js';

export interface ParseSseOptions {
  /** Largest unfinished event kept in memory, in characters. Default 1 MB; over it: `response_too_large`. */
  maxEventBytes?: number;
  /** Fail with `timeout` when no data arrives for this long (milliseconds). Default: no limit. */
  idleTimeoutMs?: number;
}

const DEFAULT_MAX_EVENT = 1024 * 1024;

function parseBlock(block: string): SseEvent | undefined {
  let event = 'message';
  const data: string[] = [];
  for (const line of block.split('\n')) {
    if (line === '' || line.startsWith(':')) continue;
    const i = line.indexOf(':');
    const field = i === -1 ? line : line.slice(0, i);
    let value = i === -1 ? '' : line.slice(i + 1);
    if (value.startsWith(' ')) value = value.slice(1);
    if (field === 'event') event = value;
    else if (field === 'data') data.push(value);
  }
  if (data.length === 0) return undefined;
  const raw = data.join('\n');
  let parsed: unknown = raw;
  try {
    parsed = JSON.parse(raw);
  } catch {
    // not JSON: keep the raw string
  }
  return { event, data: parsed };
}

/** CRLF and a lone CR both end a line. A trailing CR is kept back: its LF may come in the next chunk. */
function normalize(buffer: string): string {
  if (buffer.endsWith('\r')) return buffer.slice(0, -1).replace(/\r\n?/g, '\n') + '\r';
  return buffer.replace(/\r\n?/g, '\n');
}

/** Parse a Server-Sent Events byte stream into events. Handles chunk splits, CRLF/CR and comments. */
export async function* parseSse(
  stream: ReadableStream<Uint8Array>,
  options: ParseSseOptions = {},
): AsyncGenerator<SseEvent> {
  const maxEvent = options.maxEventBytes ?? DEFAULT_MAX_EVENT;
  const idle = options.idleTimeoutMs;
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  const readOnce = (): Promise<ReadableStreamReadResult<Uint8Array>> => {
    if (!idle) return reader.read();
    return new Promise((resolve, reject) => {
      const t = setTimeout(
        () => reject(new AgentFieldTripError(`No data received for ${idle} ms`, { status: 0, code: 'timeout' })),
        idle,
      );
      reader.read().then(
        (v) => {
          clearTimeout(t);
          resolve(v);
        },
        (e) => {
          clearTimeout(t);
          reject(e);
        },
      );
    });
  };

  try {
    for (;;) {
      const { done, value } = await readOnce();
      if (done) break;
      buffer = normalize(buffer + decoder.decode(value, { stream: true }));
      let idx: number;
      while ((idx = buffer.indexOf('\n\n')) !== -1) {
        const ev = parseBlock(buffer.slice(0, idx));
        buffer = buffer.slice(idx + 2);
        if (ev) yield ev;
      }
      if (buffer.length > maxEvent) {
        throw new AgentFieldTripError(`A stream event is larger than ${maxEvent} bytes`, {
          status: 0,
          code: 'response_too_large',
        });
      }
    }
    buffer += decoder.decode();
    const last = parseBlock(buffer.replace(/\r\n?/g, '\n'));
    if (last) yield last;
  } finally {
    await reader.cancel().catch(() => undefined);
  }
}
