import unittest

from agentfieldtrip.client import parse_sse


class SseTests(unittest.TestCase):
    def test_basic_events(self) -> None:
        raw = ["event: a\n", 'data: {"x": 1}\n', "\n", "event: b\n", "data: plain text\n", "\n"]
        ev = list(parse_sse(raw))
        self.assertEqual(
            ev, [{"event": "a", "data": {"x": 1}}, {"event": "b", "data": "plain text"}]
        )

    def test_bytes_crlf_comments_and_multiline_data(self) -> None:
        raw = [b": keepalive\r\n", b"event: m\r\n", b"data: line1\r\n", b"data: line2\r\n", b"\r\n"]
        self.assertEqual(list(parse_sse(raw)), [{"event": "m", "data": "line1\nline2"}])

    def test_default_event_name_and_trailing_event_without_blank_line(self) -> None:
        self.assertEqual(
            list(parse_sse(['data: {"a": 2}'])), [{"event": "message", "data": {"a": 2}}]
        )

    def test_ignores_empty_blocks_and_id_retry(self) -> None:
        raw = ["\n", "\n", "id: 4\n", "retry: 10\n", "data: 5\n", "\n"]
        self.assertEqual(list(parse_sse(raw)), [{"event": "message", "data": 5}])
