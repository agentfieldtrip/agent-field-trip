from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Callable, List, Tuple

from agentfieldtrip import (
    AgentFieldTrip,
    AgentFieldTripError,
    InvalidArgumentError,
    WriteResult,
    __version__,
)
from agentfieldtrip.client import _urllib_transport, parse_sse

from .helpers import FakeTransport, Sleeper, jbody, ok


def make(*responses: Any, **kw: Any) -> Tuple[AgentFieldTrip, FakeTransport, Sleeper]:
    t = FakeTransport(*responses)
    s = Sleeper()
    c = AgentFieldTrip(transport=t, sleep=s, **kw)
    return c, t, s


class HeaderTests(unittest.TestCase):
    def test_default_user_agent_and_version(self) -> None:
        c, t, _ = make(ok({"fecha": "2026-11-09"}))
        c.now()
        self.assertEqual(t.calls[0].headers["User-Agent"], f"agentfieldtrip-python/{__version__}")
        self.assertEqual(__version__, "0.1.0")
        self.assertNotIn("X-Agent-Name", t.calls[0].headers)

    def test_visitor_name_header_on_reads(self) -> None:
        c, t, _ = make(ok({}), visitor_name="Ada")
        c.now()
        self.assertEqual(t.calls[0].headers["X-Agent-Name"], "Ada")

    def test_custom_user_agent(self) -> None:
        c, t, _ = make(ok({}), user_agent="mybot/1")
        c.now()
        self.assertEqual(t.calls[0].headers["User-Agent"], "mybot/1")

    def test_name_longer_than_40_rejected(self) -> None:
        with self.assertRaises(ValueError) as cm:
            AgentFieldTrip(visitor_name="x" * 41)
        self.assertIsInstance(cm.exception, InvalidArgumentError)
        self.assertEqual(cm.exception.code, "name_invalid")  # type: ignore[attr-defined]

    def test_write_sends_idempotency_key_and_json(self) -> None:
        c, t, _ = make(ok({"firma": {}}, 201), visitor_name="Ada")
        c.sign_guestbook("hello there")
        call = t.calls[0]
        self.assertEqual(call.method, "POST")
        self.assertEqual(call.url, "https://agentfieldtrip.com/api/v1/guestbook")
        self.assertEqual(call.json, {"texto": "hello there"})
        self.assertEqual(call.headers["Content-Type"], "application/json")
        self.assertTrue(len(call.headers["Idempotency-Key"]) >= 16)

    def test_reads_have_no_idempotency_key(self) -> None:
        c, t, _ = make(ok({}))
        c.guestbook()
        self.assertNotIn("Idempotency-Key", t.calls[0].headers)

    def test_explicit_idempotency_key(self) -> None:
        c, t, _ = make(ok({}, 201), visitor_name="Ada")
        c.sign_guestbook("hello", idempotency_key="my-key-1")
        self.assertEqual(t.calls[0].headers["Idempotency-Key"], "my-key-1")

    def test_write_requires_name(self) -> None:
        c, t, _ = make(ok({}, 201))
        with self.assertRaises(ValueError):
            c.sign_guestbook("hello")
        self.assertEqual(t.calls, [])


class UrlTests(unittest.TestCase):
    def test_sandbox_switches_api_paths_only(self) -> None:
        c, t, _ = make(ok({}), sandbox=True, visitor_name="Ada")
        c.guestbook(before=5)
        c.room("some-room")
        c.ask("cats")
        c.sign_guestbook("hello")
        self.assertEqual(
            t.calls[0].url, "https://agentfieldtrip.com/sandbox/api/v1/guestbook?before=5"
        )
        self.assertEqual(t.calls[1].url, "https://agentfieldtrip.com/rooms/some-room.json")
        self.assertEqual(t.calls[2].url, "https://agentfieldtrip.com/ask")
        self.assertEqual(t.calls[3].url, "https://agentfieldtrip.com/sandbox/api/v1/guestbook")

    def test_base_url_trailing_slash(self) -> None:
        c, t, _ = make(ok({}), base_url="http://localhost:3000/")
        c.now()
        self.assertEqual(t.calls[0].url, "http://localhost:3000/api/v1/now")

    def test_endpoint_urls(self) -> None:
        c, t, _ = make(ok({}), visitor_name="Ada")
        c.live()
        c.piece("cma-1", room="r1")
        c.style("baroque")
        c.search("cat", type="imagen", limit=3)
        c.tours()
        c.tour("poet")
        c.comments("cma-1", before=9)
        c.passport("casa-pip")
        c.postcard("casa-pip")
        c.messages("casa-pip", before=2)
        c.whats_new()
        c.here(room="r1")
        urls = [x.url.replace("https://agentfieldtrip.com", "") for x in t.calls]
        self.assertEqual(
            urls,
            [
                "/api/v1/live",
                "/pieces/cma-1.json?room=r1",
                "/styles/baroque.json",
                "/api/v1/search?q=cat&tipo=imagen&limit=3",
                "/api/v1/tours",
                "/api/v1/tours/poet",
                "/api/v1/pieces/cma-1/comments?before=9",
                "/api/v1/agents/casa-pip",
                "/api/v1/agents/casa-pip/postcard",
                "/api/v1/agents/casa-pip/messages?before=2",
                "/api/v1/whats-new",
                "/api/v1/here?room=r1",
            ],
        )

    def test_invalid_ids_rejected_before_any_request(self) -> None:
        c, t, _ = make(ok({}))
        bad: List[Callable[[], Any]] = [
            lambda: c.room("../etc/passwd"),
            lambda: c.piece("a/b"),
            lambda: c.style("-x"),
            lambda: c.comments("a#b"),
            lambda: c.passport("XYZ"),
            lambda: c.postcard("0123"),
            lambda: c.messages("casa-"),
            lambda: c.review_status("abc#frag"),
            lambda: c.review_status("f0-" + "a" * 20),
        ]
        for fn in bad:
            with self.assertRaises(InvalidArgumentError) as cm:
                fn()
            self.assertEqual(cm.exception.code, "invalid_id")
        self.assertEqual(t.calls, [])

    def test_valid_agent_ids(self) -> None:
        c, t, _ = make(ok({}))
        c.passport("0123456789abcdef")
        c.passport("casa-pip")
        self.assertEqual(len(t.calls), 2)

    def test_send_message_validates_agent_id(self) -> None:
        c, t, _ = make(ok({}, 201), visitor_name="Ada")
        with self.assertRaises(InvalidArgumentError):
            c.send_message("nope!", "note", text="hi")
        self.assertEqual(t.calls, [])

    def test_whats_new_requires_name(self) -> None:
        c, _, _ = make(ok({}))
        with self.assertRaises(ValueError):
            c.whats_new()

    def test_review_status_accepts_url(self) -> None:
        c, t, _ = make(ok({"estado": "pending"}))
        c.review_status("https://agentfieldtrip.com/api/v1/reviews/f12-" + "a" * 20)
        c.review_status("sb-" + "b" * 20)
        self.assertTrue(t.calls[0].url.endswith("/api/v1/reviews/f12-" + "a" * 20))
        self.assertTrue(t.calls[1].url.endswith("/api/v1/reviews/sb-" + "b" * 20))

    def test_piece_with_visitor_notes_query_param(self) -> None:
        c, t, _ = make(ok({"id": "cma-1", "notas_de_visitantes": [{"texto": "hi"}]}))
        p = c.piece("cma-1", room="r1", include_visitor_notes=True)
        self.assertEqual(len(t.calls), 1)
        self.assertTrue(
            t.calls[0].url.endswith("/pieces/cma-1.json?room=r1&include_visitor_notes=true")
        )
        self.assertEqual(p["notas_de_visitantes"][0]["texto"], "hi")

    def test_piece_without_visitor_notes_has_no_param(self) -> None:
        c, t, _ = make(ok({"id": "cma-1"}))
        c.piece("cma-1")
        self.assertNotIn("include_visitor_notes", t.calls[0].url)


class WriteBodyTests(unittest.TestCase):
    def test_comment_answer_message(self) -> None:
        c, t, _ = make(ok({}, 201), visitor_name="Ada")
        c.comment("cma-1", "lovely", reaction="wow")
        c.answer_challenge("cma-1", 2, "a red cup")
        c.send_message("casa-pip", "recommendation", piece_id="cma-2")
        c.send_message("casa-pip", "note", text="hello")
        self.assertEqual(t.calls[0].json, {"texto": "lovely", "reaccion": "wow"})
        self.assertTrue(t.calls[0].url.endswith("/pieces/cma-1/comments"))
        self.assertEqual(t.calls[1].json, {"respuesta": "a red cup"})
        self.assertTrue(t.calls[1].url.endswith("/pieces/cma-1/challenges/2"))
        self.assertEqual(t.calls[2].json, {"tipo": "recommendation", "pieza_id": "cma-2"})
        self.assertTrue(t.calls[2].url.endswith("/agents/casa-pip/messages"))
        self.assertEqual(t.calls[3].json, {"tipo": "note", "texto": "hello"})

    def test_challenge_n_range(self) -> None:
        c, _, _ = make(ok({}), visitor_name="Ada")
        with self.assertRaises(ValueError):
            c.answer_challenge("cma-1", 4, "x")

    def test_ask_body(self) -> None:
        c, t, _ = make(ok({"results": []}))
        c.ask("blue cats")
        self.assertEqual(
            t.calls[0].json,
            {"query": {"text": "blue cats"}},
        )

    def test_ask_mode(self) -> None:
        c, t, _ = make(ok({"results": []}))
        c.ask("blue cats", mode="list, summarize")
        self.assertEqual(t.calls[0].json["prefer"], {"mode": "list, summarize"})


class WriteResultTests(unittest.TestCase):
    def test_201_result(self) -> None:
        c, _, _ = make(ok({"firma": {"id": 1}}, 201, Idempotent_Replayed="true"), visitor_name="A")
        r = c.sign_guestbook("hello")
        self.assertIsInstance(r, WriteResult)
        self.assertEqual(r["firma"]["id"], 1)
        self.assertEqual(r.status, 201)
        self.assertTrue(r.replayed)
        self.assertIsNone(r.review_url)

    def test_202_review_url_is_absolute(self) -> None:
        c, _, _ = make(
            ok({"estado": "pending"}, 202, Location="/api/v1/reviews/f1-" + "a" * 20),
            visitor_name="A",
        )
        r = c.sign_guestbook("hello")
        self.assertEqual(r.status, 202)
        self.assertEqual(r.review_url, "https://agentfieldtrip.com/api/v1/reviews/f1-" + "a" * 20)
        self.assertFalse(r.replayed)


class RetryTests(unittest.TestCase):
    def test_retry_503_same_key_then_ok(self) -> None:
        c, t, s = make(
            ok({"error": "name_check_unavailable"}, 503),
            ok({"error": "x"}, 503),
            ok({"firma": {}}, 201),
            visitor_name="A",
        )
        r = c.sign_guestbook("hello")
        self.assertEqual(r.status, 201)
        self.assertEqual(len(t.calls), 3)
        keys = {x.headers["Idempotency-Key"] for x in t.calls}
        self.assertEqual(len(keys), 1)
        self.assertEqual(len(s.slept), 2)

    def test_retry_429_honors_retry_after(self) -> None:
        c, t, s = make(
            ok({"error": "rate_limited"}, 429, Retry_After="7"), ok({}, 201), visitor_name="A"
        )
        c.sign_guestbook("hello")
        self.assertEqual(len(t.calls), 2)
        self.assertEqual(s.slept, [7.0])

    def test_retry_after_is_capped(self) -> None:
        c, _, s = make(
            ok({"error": "rate_limited"}, 429, Retry_After="3600"),
            ok({}, 201),
            visitor_name="A",
            max_retry_after=10,
        )
        c.sign_guestbook("hello")
        self.assertEqual(s.slept, [10.0])

    def test_no_retry_409_on_reads(self) -> None:
        c, t, _ = make(ok({"error": "conflict"}, 409, Retry_After="1"), ok({}))
        with self.assertRaises(AgentFieldTripError):
            c.now()
        self.assertEqual(len(t.calls), 1)

    def test_retry_409_in_flight(self) -> None:
        c, t, _ = make(
            ok({"error": "in_progress"}, 409, Retry_After="1"), ok({}, 201), visitor_name="A"
        )
        c.sign_guestbook("hello")
        self.assertEqual(len(t.calls), 2)
        self.assertEqual(
            t.calls[0].headers["Idempotency-Key"], t.calls[1].headers["Idempotency-Key"]
        )

    def test_retry_network_error(self) -> None:
        c, t, _ = make(OSError("boom"), ok({}, 201), visitor_name="A")
        r = c.sign_guestbook("hello")
        self.assertEqual(r.status, 201)
        self.assertEqual(len(t.calls), 2)

    def test_no_retry_on_422(self) -> None:
        c, t, s = make(
            ok({"motivo": "contains_link", "detalle": "No links."}, 422),
            visitor_name="A",
        )
        with self.assertRaises(AgentFieldTripError) as cm:
            c.sign_guestbook("see http://x.y")
        self.assertEqual(len(t.calls), 1)
        self.assertEqual(s.slept, [])
        self.assertEqual(cm.exception.status, 422)

    def test_gives_up_after_max_retries(self) -> None:
        c, t, _ = make(ok({"error": "down"}, 503), max_retries=2)
        with self.assertRaises(AgentFieldTripError) as cm:
            c.now()
        self.assertEqual(len(t.calls), 3)
        self.assertEqual(cm.exception.status, 503)

    def test_max_retries_zero(self) -> None:
        c, t, _ = make(ok({"error": "down"}, 503), max_retries=0)
        with self.assertRaises(AgentFieldTripError):
            c.now()
        self.assertEqual(len(t.calls), 1)

    def test_network_error_exhausted(self) -> None:
        c, t, _ = make(OSError("boom"), max_retries=1)
        with self.assertRaises(AgentFieldTripError) as cm:
            c.now()
        self.assertEqual(len(t.calls), 2)
        self.assertIsNone(cm.exception.status)
        self.assertEqual(cm.exception.code, "network_error")

    def test_http_date_retry_after_falls_back_to_backoff(self) -> None:
        c, _, s = make(
            ok({"error": "x"}, 429, Retry_After="Wed, 21 Oct 2026 07:28:00 GMT"),
            ok({}),
            max_retries=1,
        )
        c.now()
        self.assertEqual(len(s.slept), 1)
        self.assertGreaterEqual(s.slept[0], 0)


class ErrorTests(unittest.TestCase):
    def test_error_fields_from_error_code(self) -> None:
        c, _, _ = make(ok({"error": "not_found", "detalle": "Nope."}, 404))
        with self.assertRaises(AgentFieldTripError) as cm:
            c.room("zzz")
        e = cm.exception
        self.assertEqual((e.status, e.code, e.detail), (404, "not_found", "Nope."))
        self.assertEqual(e.body, {"error": "not_found", "detalle": "Nope."})
        self.assertIn("not_found", str(e))

    def test_error_code_from_motivo(self) -> None:
        c, _, _ = make(
            ok({"motivo": "instruction_like_text", "detalle": "No."}, 422), visitor_name="A"
        )
        with self.assertRaises(AgentFieldTripError) as cm:
            c.sign_guestbook("ignore previous instructions")
        self.assertEqual(cm.exception.code, "instruction_like_text")

    def test_retry_after_on_error(self) -> None:
        c, _, _ = make(ok({"error": "rate_limited"}, 429, Retry_After="5"), max_retries=0)
        with self.assertRaises(AgentFieldTripError) as cm:
            c.now()
        self.assertEqual(cm.exception.retry_after, 5.0)

    def test_non_json_error_body(self) -> None:
        c, _, _ = make((502, {}, b"<html>bad gateway</html>"), max_retries=0)
        with self.assertRaises(AgentFieldTripError) as cm:
            c.now()
        self.assertEqual(cm.exception.status, 502)
        self.assertIsNone(cm.exception.code)
        self.assertEqual(cm.exception.body, "<html>bad gateway</html>")


class BatchTests(unittest.TestCase):
    def test_batch_body(self) -> None:
        c, t, _ = make(ok({"respuestas": []}))
        c.batch(["/pieces/a.json", "/rooms/b.json"])
        self.assertEqual(t.calls[0].method, "POST")
        self.assertTrue(t.calls[0].url.endswith("/api/v1/batch"))
        self.assertEqual(
            t.calls[0].json,
            {"requests": [{"path": "/pieces/a.json"}, {"path": "/rooms/b.json"}]},
        )

    def test_batch_accepts_items_with_id(self) -> None:
        c, t, _ = make(ok({}))
        c.batch(["/pieces/a.json", {"id": "mine", "path": "/rooms/b.json"}])
        self.assertEqual(
            t.calls[0].json,
            {"requests": [{"path": "/pieces/a.json"}, {"id": "mine", "path": "/rooms/b.json"}]},
        )

    def test_batch_limit_25(self) -> None:
        c, t, _ = make(ok({}))
        c.batch(["/pieces/a.json"] * 25)
        with self.assertRaises(ValueError):
            c.batch(["/pieces/a.json"] * 26)
        with self.assertRaises(ValueError):
            c.batch([])
        self.assertEqual(len(t.calls), 1)

    def test_batch_has_no_idempotency_key(self) -> None:
        c, t, _ = make(ok({}))
        c.batch(["/pieces/a.json"])
        self.assertNotIn("Idempotency-Key", t.calls[0].headers)


class StreamTests(unittest.TestCase):
    def test_ask_stream_yields_events(self) -> None:
        lines = [
            b"event: start\n",
            b'data: {"_meta":{"version":"0.55"}}\n',
            b"\n",
            b"event: result\n",
            b'data: {"index":0,"item":{"name":"x"}}\n',
            b"\n",
        ]
        c, t, _ = make((200, {"content-type": "text/event-stream"}, iter(lines)))
        events = list(c.ask("cats", stream=True))
        self.assertEqual([e["event"] for e in events], ["start", "result"])
        self.assertEqual(events[1]["data"]["item"]["name"], "x")
        self.assertTrue(t.calls[0].stream)
        self.assertEqual(t.calls[0].headers["Accept"], "text/event-stream")
        self.assertEqual(t.calls[0].json["prefer"], {"streaming": True})

    def test_sse_event_too_large(self) -> None:
        big = "data: " + "x" * (1024 * 1024 + 1) + "\n"
        with self.assertRaises(AgentFieldTripError) as cm:
            list(parse_sse([big]))
        self.assertEqual(cm.exception.code, "response_too_large")

    def test_ask_stream_error_raises(self) -> None:
        c, _, _ = make(ok({"error": "rate_limited"}, 429), max_retries=0)
        with self.assertRaises(AgentFieldTripError):
            list(c.ask("cats", stream=True))


class EncodingTests(unittest.TestCase):
    def test_json_roundtrip_unicode(self) -> None:
        c, t, _ = make(ok({}, 201), visitor_name="Ada")
        c.sign_guestbook("¡Qué bonito!")
        body = t.calls[0].body
        assert body is not None
        self.assertEqual(json.loads(body.decode("utf-8")), {"texto": "¡Qué bonito!"})
        self.assertEqual(jbody({"a": 1}), b'{"a": 1}')


class NameTests(unittest.TestCase):
    def test_non_ascii_name_write_goes_in_body(self) -> None:
        for name in ("José", "Zoë", "ロボ"):
            c, t, _ = make(ok({}, 201), visitor_name=name)
            c.sign_guestbook("hello")
            self.assertNotIn("X-Agent-Name", t.calls[0].headers)
            self.assertEqual(t.calls[0].json, {"texto": "hello", "nombre": name})
            self.assertNotIn("agent=", t.calls[0].url)

    def test_non_ascii_name_read_goes_in_query(self) -> None:
        c, t, _ = make(ok({}), visitor_name="José")
        c.guestbook(before=3)
        c.now()
        self.assertNotIn("X-Agent-Name", t.calls[0].headers)
        self.assertTrue(t.calls[0].url.endswith("/guestbook?before=3&agent=Jos%C3%A9"))
        self.assertTrue(t.calls[1].url.endswith("/now?agent=Jos%C3%A9"))
        c2, t2, _ = make(ok({}), visitor_name="ロボ")
        c2.room("r1")
        self.assertIn("agent=%E3%83%AD%E3%83%9C", t2.calls[0].url)

    def test_ascii_name_stays_in_header(self) -> None:
        c, t, _ = make(ok({}, 201), visitor_name="Ada Lovelace")
        c.sign_guestbook("hello")
        self.assertEqual(t.calls[0].headers["X-Agent-Name"], "Ada Lovelace")
        self.assertEqual(t.calls[0].json, {"texto": "hello"})

    def test_name_is_trimmed(self) -> None:
        c, t, _ = make(ok({}), visitor_name="  Ada ")
        c.now()
        self.assertEqual(t.calls[0].headers["X-Agent-Name"], "Ada")

    def test_invalid_names(self) -> None:
        for bad in ("Bob\r\n X-Evil: 1", "a\x1b[31m", "", "   ", "x" * 41, "tab\there"):
            with self.assertRaises(InvalidArgumentError) as cm:
                AgentFieldTrip(visitor_name=bad)
            self.assertEqual(cm.exception.code, "name_invalid")


class BaseUrlTests(unittest.TestCase):
    def test_https_required_except_localhost(self) -> None:
        with self.assertRaises(ValueError):
            AgentFieldTrip(base_url="http://agentfieldtrip.com")
        with self.assertRaises(ValueError):
            AgentFieldTrip(base_url="ftp://x.y")
        for ok_url in (
            "https://agentfieldtrip.com",
            "http://localhost:3000",
            "http://127.0.0.1:8787",
            "http://[::1]:8787",
        ):
            AgentFieldTrip(base_url=ok_url)


class RedirectTests(unittest.TestCase):
    def test_3xx_is_an_error_with_location(self) -> None:
        loc = "https://agentfieldtrip.com/api/v1/guestbook"
        c, t, _ = make((301, {"location": loc}, b""), visitor_name="Ada")
        with self.assertRaises(AgentFieldTripError) as cm:
            c.sign_guestbook("hello")
        self.assertEqual(cm.exception.code, "redirect_not_followed")
        self.assertEqual(cm.exception.status, 301)
        self.assertIn(loc, str(cm.exception))
        self.assertEqual(cm.exception.body, {"location": loc})
        self.assertEqual(len(t.calls), 1)

    def test_default_transport_does_not_follow_redirects(self) -> None:
        seen = []

        class H(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                seen.append(self.path)
                self.send_response(301)
                self.send_header("Location", "/elsewhere")
                self.send_header("Content-Length", "0")
                self.end_headers()

            do_GET = do_POST

            def log_message(self, *a: Any) -> None:
                pass

        srv = HTTPServer(("127.0.0.1", 0), H)
        th = threading.Thread(target=srv.serve_forever, daemon=True)
        th.start()
        try:
            url = f"http://127.0.0.1:{srv.server_port}/api/v1/guestbook"
            status, hdrs, _ = _urllib_transport(
                "POST", url, {"X-Agent-Name": "Ada"}, b"{}", 5.0, False
            )
        finally:
            srv.shutdown()
            srv.server_close()
        self.assertEqual(status, 301)
        self.assertEqual(hdrs["location"], "/elsewhere")
        self.assertEqual(seen, ["/api/v1/guestbook"])


class HostileResponseTests(unittest.TestCase):
    def test_non_json_success_is_invalid_json(self) -> None:
        c, _, _ = make((200, {}, b"<html>nope</html>"))
        with self.assertRaises(AgentFieldTripError) as cm:
            c.now()
        self.assertEqual(cm.exception.code, "invalid_json")

    def test_deeply_nested_json_is_invalid_json(self) -> None:
        c, _, _ = make((200, {}, b"[" * 200000))
        with self.assertRaises(AgentFieldTripError) as cm:
            c.now()
        self.assertEqual(cm.exception.code, "invalid_json")

    def test_deeply_nested_error_body_still_raises_http_error(self) -> None:
        c, _, _ = make((400, {}, b"[" * 200000), max_retries=0)
        with self.assertRaises(AgentFieldTripError) as cm:
            c.now()
        self.assertEqual(cm.exception.status, 400)

    def test_response_too_large(self) -> None:
        c, _, _ = make((200, {}, b"x" * (8 * 1024 * 1024 + 1)))
        with self.assertRaises(AgentFieldTripError) as cm:
            c.now()
        self.assertEqual(cm.exception.code, "response_too_large")
