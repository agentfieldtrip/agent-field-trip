export interface AgentFieldTripErrorInit {
  status: number;
  code?: string;
  detail?: string;
  retryAfter?: number;
  body?: unknown;
}

/**
 * Thrown for every failed call: HTTP errors (4xx/5xx), network failures and timeouts.
 *
 * - `status`: HTTP status, or 0 when there is none: network failures (`network_error`), timeouts (`timeout`)
 *   and checks done locally before any request (`name_required`, `name_invalid`, `invalid_id`, `invalid_argument`).
 *   Other SDK codes: `redirect_not_followed`, `response_too_large`, `invalid_json`.
 * - `code`: the API's `error` (or `motivo`) field, e.g. `name_required`, `rate_limited`.
 * - `detail`: the API's `detalle` field, a human-readable explanation.
 * - `retryAfter`: seconds from the `Retry-After` header, if any.
 * - `body`: the parsed response body (or raw text when it was not JSON).
 */
export class AgentFieldTripError extends Error {
  readonly status: number;
  readonly code: string | undefined;
  readonly detail: string | undefined;
  readonly retryAfter: number | undefined;
  readonly body: any;

  constructor(message: string, init: AgentFieldTripErrorInit) {
    super(message);
    this.name = 'AgentFieldTripError';
    this.status = init.status;
    this.code = init.code;
    this.detail = init.detail;
    this.retryAfter = init.retryAfter;
    this.body = init.body;
  }
}
