export function mockFetch(handlers) {
  const calls = [];
  const fn = async (url, init = {}) => {
    const call = {
      url: String(url),
      method: init.method ?? 'GET',
      headers: new Headers(init.headers),
      body: init.body === undefined ? undefined : JSON.parse(init.body),
    };
    calls.push(call);
    const h = handlers[Math.min(calls.length - 1, handlers.length - 1)];
    if (h instanceof Error) throw h;
    return h(call, init);
  };
  fn.calls = calls;
  return fn;
}

export const json = (obj, status = 200, headers = {}) => () =>
  new Response(JSON.stringify(obj), {
    status,
    headers: { 'content-type': 'application/json', ...headers },
  });

export function sleeper() {
  const sleeps = [];
  const sleep = async (ms) => {
    sleeps.push(ms);
  };
  sleep.sleeps = sleeps;
  return sleep;
}

export function sseResponse(chunks) {
  const enc = new TextEncoder();
  const stream = new ReadableStream({
    start(c) {
      for (const ch of chunks) c.enqueue(enc.encode(ch));
      c.close();
    },
  });
  return new Response(stream, { status: 200, headers: { 'content-type': 'text/event-stream' } });
}
