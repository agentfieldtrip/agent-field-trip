from __future__ import annotations

import io
import json
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from typing import Any, Dict, List, Tuple
from unittest import mock

from agentfieldtrip import __version__
from agentfieldtrip.cli import build_parser, main

from .helpers import FakeTransport, Sleeper, ok


def run(
    argv: List[str], *responses: Any, env: Dict[str, str] | None = None
) -> Tuple[int, str, str, FakeTransport]:
    t = FakeTransport(*responses)
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.dict(os.environ, env or {}, clear=False):
        for var in ("AGENTFIELDTRIP_NAME", "AGENTFIELDTRIP_BASE_URL"):
            if env is None or var not in env:
                os.environ.pop(var, None)
        with redirect_stdout(out), redirect_stderr(err):
            code = main(argv, transport=t, sleep=Sleeper())
    return code, out.getvalue(), err.getvalue(), t


NOW = {
    "fecha": "2026-11-09",
    "abiertas": [
        {"id": "maps", "titulo": {"en": "Maps", "es": "Mapas"}, "cierra": "2026-11-16", "n": 12}
    ],
    "proximas": [],
}


class ParsingTests(unittest.TestCase):
    def test_version(self) -> None:
        out = io.StringIO()
        with redirect_stdout(out), self.assertRaises(SystemExit) as cm:
            build_parser().parse_args(["--version"])
        self.assertEqual(cm.exception.code, 0)
        self.assertIn(__version__, out.getvalue())

    def test_no_command_is_usage_error(self) -> None:
        code, _, err, _ = run([], ok({}))
        self.assertEqual(code, 2)
        self.assertTrue(err)

    def test_unknown_command_is_usage_error(self) -> None:
        code, _, _, _ = run(["nope"], ok({}))
        self.assertEqual(code, 2)

    def test_missing_argument_is_usage_error(self) -> None:
        code, _, _, t = run(["room"], ok({}))
        self.assertEqual(code, 2)
        self.assertEqual(t.calls, [])

    def test_bad_reaction_is_usage_error(self) -> None:
        code, _, _, _ = run(["--name", "A", "comment", "p", "hi", "--reaction", "zzz"], ok({}))
        self.assertEqual(code, 2)

    def test_answer_n_not_int(self) -> None:
        code, _, _, _ = run(["--name", "A", "answer", "p", "x", "ans"], ok({}))
        self.assertEqual(code, 2)


class ReadCommandTests(unittest.TestCase):
    def test_now_json(self) -> None:
        code, out, _, t = run(["--json", "now"], ok(NOW))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), NOW)
        self.assertTrue(t.calls[0].url.endswith("/api/v1/now"))

    def test_rooms_lists_open_rooms(self) -> None:
        code, out, _, _ = run(["rooms"], ok(NOW))
        self.assertEqual(code, 0)
        self.assertIn("maps", out)
        self.assertIn("Maps", out)

    def test_room_piece_style_search_tours(self) -> None:
        code, _, _, t = run(
            ["room", "maps"], ok({"id": "maps", "titulo": {"en": "Maps"}, "recorrido": []})
        )
        self.assertEqual(code, 0)
        self.assertTrue(t.calls[0].url.endswith("/rooms/maps.json"))
        code, _, _, t = run(
            ["piece", "cma-1", "--room", "maps"], ok({"id": "cma-1", "titulo": {"en": "T"}})
        )
        self.assertTrue(t.calls[0].url.endswith("/pieces/cma-1.json?room=maps"))
        code, _, _, t = run(["style", "baroque"], ok({"id": "baroque"}))
        self.assertTrue(t.calls[0].url.endswith("/styles/baroque.json"))
        code, out, _, t = run(
            ["search", "blue", "cat"],
            ok({"resultados": [{"id": "1", "titulo": "A cat", "fuente": "x"}]}),
        )
        self.assertIn("q=blue+cat", t.calls[0].url)
        self.assertIn("A cat", out)
        code, _, _, t = run(["tours"], ok({"tours": []}))
        code, _, _, t = run(["tour", "poet"], ok({"itinerario": []}))
        self.assertTrue(t.calls[0].url.endswith("/tours/poet"))
        self.assertEqual(code, 0)

    def test_tour_bad_personality_is_usage_error(self) -> None:
        code, _, _, _ = run(["tour", "pirate"], ok({}))
        self.assertEqual(code, 2)

    def test_guestbook_postcard_review(self) -> None:
        code, _, _, t = run(["guestbook"], ok({"firmas": [], "siguiente": None}))
        self.assertEqual(code, 0)
        code, _, _, t = run(["postcard", "casa-pip"], ok({"titulo": "Hi"}))
        self.assertTrue(t.calls[0].url.endswith("/agents/casa-pip/postcard"))
        code, _, _, t = run(["review", "sb-" + "a" * 20], ok({"estado": "pending"}))
        self.assertTrue(t.calls[0].url.endswith("/reviews/sb-" + "a" * 20))

    def test_ask_stream_prints_events(self) -> None:
        lines = [b"event: result\n", b'data: {"index":0,"item":{"name":"Cat"}}\n', b"\n"]
        code, out, _, _ = run(["ask", "cats", "--stream"], (200, {}, iter(lines)))
        self.assertEqual(code, 0)
        self.assertIn("Cat", out)

    def test_ask_plain(self) -> None:
        code, out, _, _ = run(["--json", "ask", "cats"], ok({"results": []}))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {"results": []})

    def test_global_flags(self) -> None:
        code, _, _, t = run(
            ["--sandbox", "--base-url", "http://localhost:1", "--name", "Ada", "guestbook"],
            ok({"firmas": []}),
        )
        self.assertEqual(code, 0)
        self.assertEqual(t.calls[0].url, "http://localhost:1/sandbox/api/v1/guestbook")
        self.assertEqual(t.calls[0].headers["X-Agent-Name"], "Ada")

    def test_env_name(self) -> None:
        code, _, _, t = run(
            ["guestbook"], ok({"firmas": []}), env={"AGENTFIELDTRIP_NAME": "EnvBot"}
        )
        self.assertEqual(code, 0)
        self.assertEqual(t.calls[0].headers["X-Agent-Name"], "EnvBot")


class ExitCodeTests(unittest.TestCase):
    def test_api_error_exit_1(self) -> None:
        code, out, err, _ = run(
            ["room", "zzz"], ok({"error": "not_found", "detalle": "No such room."}, 404)
        )
        self.assertEqual(code, 1)
        self.assertIn("not_found", err)
        self.assertEqual(out, "")

    def test_api_error_json_mode(self) -> None:
        code, out, err, _ = run(["--json", "room", "zzz"], ok({"error": "not_found"}, 404))
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(err)["status"], 404)


class WriteCommandTests(unittest.TestCase):
    def test_write_without_name_is_usage_error(self) -> None:
        code, _, err, t = run(["sign", "hello"], ok({}, 201))
        self.assertEqual(code, 2)
        self.assertIn("--name", err)
        self.assertEqual(t.calls, [])

    def test_sign_prints_public_notice_and_writes(self) -> None:
        code, out, err, t = run(["--name", "Ada", "sign", "hello"], ok({"firma": {"id": 3}}, 201))
        self.assertEqual(code, 0)
        self.assertIn("public", err.lower())
        self.assertIn("--sandbox", err)
        self.assertEqual(t.calls[0].json, {"texto": "hello"})
        self.assertIn("201", out)

    def test_sandbox_notice_differs(self) -> None:
        code, _, err, t = run(["--name", "Ada", "--sandbox", "sign", "hello"], ok({}, 201))
        self.assertEqual(code, 0)
        self.assertIn("sandbox", err.lower())
        self.assertIn("/sandbox/api/v1/guestbook", t.calls[0].url)

    def test_comment_and_answer(self) -> None:
        code, _, _, t = run(
            ["--name", "Ada", "comment", "cma-1", "nice", "--reaction", "wow"], ok({}, 201)
        )
        self.assertEqual(code, 0)
        self.assertEqual(t.calls[0].json, {"texto": "nice", "reaccion": "wow"})
        code, _, _, t = run(["--name", "Ada", "answer", "cma-1", "2", "a cup"], ok({}, 200))
        self.assertEqual(code, 0)
        self.assertTrue(t.calls[0].url.endswith("/pieces/cma-1/challenges/2"))

    def test_json_write_includes_meta(self) -> None:
        code, out, _, _ = run(
            ["--json", "--name", "Ada", "sign", "hello"],
            ok({"estado": "pending"}, 202, Location="/api/v1/reviews/f1-" + "a" * 20),
        )
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["status"], 202)
        self.assertTrue(data["review_url"].endswith("/reviews/f1-" + "a" * 20))
        self.assertEqual(data["body"], {"estado": "pending"})

    def test_moderation_rejection_exit_1(self) -> None:
        code, _, err, _ = run(
            ["--name", "Ada", "sign", "see http://x.y"],
            ok({"motivo": "contains_link", "detalle": "No links."}, 422),
        )
        self.assertEqual(code, 1)
        self.assertIn("contains_link", err)


class JoinAndEnvTests(unittest.TestCase):
    def test_multi_word_writes_are_joined(self) -> None:
        code, _, _, t = run(["--name", "Ada", "sign", "hello", "there"], ok({}, 201))
        self.assertEqual(code, 0)
        self.assertEqual(t.calls[0].json, {"texto": "hello there"})
        code, _, _, t = run(["--name", "Ada", "comment", "cma-1", "so", "nice"], ok({}, 201))
        self.assertEqual(t.calls[0].json, {"texto": "so nice"})
        code, _, _, t = run(["--name", "Ada", "answer", "cma-1", "1", "a", "red", "cup"], ok({}))
        self.assertEqual(t.calls[0].json, {"respuesta": "a red cup"})

    def test_env_base_url(self) -> None:
        code, _, _, t = run(
            ["guestbook"],
            ok({"firmas": []}),
            env={"AGENTFIELDTRIP_BASE_URL": "http://localhost:9"},
        )
        self.assertEqual(code, 0)
        self.assertTrue(t.calls[0].url.startswith("http://localhost:9/api/v1/guestbook"))

    def test_flag_beats_env_base_url(self) -> None:
        code, _, _, t = run(
            ["--base-url", "http://localhost:5", "guestbook"],
            ok({"firmas": []}),
            env={"AGENTFIELDTRIP_BASE_URL": "http://localhost:9"},
        )
        self.assertTrue(t.calls[0].url.startswith("http://localhost:5/"))

    def test_insecure_base_url_is_usage_error(self) -> None:
        code, _, err, t = run(["--base-url", "http://agentfieldtrip.com", "now"], ok({}))
        self.assertEqual(code, 2)
        self.assertEqual(t.calls, [])

    def test_invalid_name_is_usage_error(self) -> None:
        code, _, err, t = run(["--name", "Bob\r\nX: 1", "sign", "hi"], ok({}, 201))
        self.assertEqual(code, 2)
        self.assertEqual(t.calls, [])


class Utf8OutputTests(unittest.TestCase):
    def test_main_reconfigures_streams_to_utf8(self) -> None:
        class Stream(io.StringIO):
            def __init__(self) -> None:
                super().__init__()
                self.kwargs: Dict[str, Any] = {}

            def reconfigure(self, **kw: Any) -> None:
                self.kwargs = kw

        out, err = Stream(), Stream()
        t = FakeTransport(ok({"resultados": [{"id": "1", "titulo": "ロボ"}]}))
        with mock.patch("sys.stdout", out), mock.patch("sys.stderr", err):
            code = main(["search", "x"], transport=t, sleep=Sleeper())
        self.assertEqual(code, 0)
        self.assertEqual(out.kwargs, {"encoding": "utf-8", "errors": "replace"})
        self.assertEqual(err.kwargs, {"encoding": "utf-8", "errors": "replace"})
