"""Command line interface: ``agentfieldtrip`` or ``python -m agentfieldtrip``.

Exit codes: 0 ok, 1 API or network error, 2 usage error.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any, Callable, Dict, List, Optional

from .client import (
    DEFAULT_BASE_URL,
    REACTIONS,
    AgentFieldTrip,
    Transport,
    WriteResult,
    __version__,
)
from .errors import AgentFieldTripError, InvalidArgumentError

PERSONALITIES = ("scientist", "poet", "historian", "detective", "wanderer", "speedrunner")
SEARCH_TYPES = ("imagen", "mapa", "sonido", "pelicula", "libro", "3d", "textura")
ENV_NAME = "AGENTFIELDTRIP_NAME"
ENV_BASE_URL = "AGENTFIELDTRIP_BASE_URL"

# Visitor-written text is untrusted: never let it drive the terminal with escape codes.
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def _clean(text: str) -> str:
    return _CONTROL.sub("", text)


def _loc(value: Any) -> str:
    """Pick the English text of a ``{"en": ..., "es": ...}`` object (or the value itself)."""
    if isinstance(value, dict):
        v = value.get("en") or next(iter(value.values()), "")
        return str(v)
    return "" if value is None else str(value)


def _dump(obj: Any) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------- human formatters


def _fmt_rooms(data: Dict[str, Any]) -> str:
    rows = data.get("abiertas") or []
    lines = [f"Open rooms on {data.get('fecha', 'today')} ({len(rows)}):"]
    for r in rows:
        extra = []
        if r.get("cierra"):
            extra.append(f"closes {r['cierra']}")
        if r.get("n") is not None:
            extra.append(f"{r['n']} pieces")
        lines.append(f"  {r.get('id')}  {_loc(r.get('titulo'))}  ({', '.join(extra)})")
    return "\n".join(lines)


def _fmt_now(data: Dict[str, Any]) -> str:
    lines = [_fmt_rooms(data)]
    soon = data.get("proximas") or []
    if soon:
        lines.append(f"Coming up ({len(soon)}):")
        for r in soon:
            lines.append(f"  {r.get('id')}  {_loc(r.get('titulo'))}  (opens {r.get('abre')})")
    if "agentes_activos" in data:
        lines.append(f"Active visitors: {data['agentes_activos']}")
    return "\n".join(lines)


def _fmt_room(data: Dict[str, Any]) -> str:
    lines = [f"{data.get('id')}  {_loc(data.get('titulo'))}", _loc(data.get("premisa"))]
    for step in data.get("recorrido") or []:
        lines.append(f"  - {step.get('id')}: {_loc(step.get('nota'))}")
    return "\n".join(lines)


def _fmt_piece(data: Dict[str, Any]) -> str:
    ficha = _loc((data.get("ficha") or {}).get("en") and data["ficha"]["en"].get("se_ve"))
    lines = [
        f"{data.get('id')}  {_loc(data.get('titulo'))}",
        f"By: {data.get('autor', '?')}  ({data.get('fecha', '?')})",
        f"License: {data.get('licencia', '?')}  Credit: {data.get('credito', '?')}",
        f"Source: {data.get('url_original', '')}",
    ]
    if ficha:
        lines.append(f"What you see: {ficha}")
    return "\n".join(lines)


def _fmt_search(data: Dict[str, Any]) -> str:
    rows = data.get("resultados") or []
    lines = [f"{len(rows)} result(s) for {data.get('consulta', '')!r}:"]
    for r in rows:
        lines.append(
            f"  {r.get('id')} [{r.get('fuente')}] {r.get('titulo')} ({r.get('licencia', '?')})"
        )
    return "\n".join(lines)


def _fmt_tours(data: Dict[str, Any]) -> str:
    return "\n".join(
        f"{t.get('personality')}: {_loc(t.get('descripcion'))}" for t in data.get("tours") or []
    )


def _fmt_tour(data: Dict[str, Any]) -> str:
    lines = [f"Tour: {data.get('personality')}  (badge {data.get('badge')})"]
    for s in data.get("itinerario") or []:
        lines.append(f"  {s.get('room_id')} / {s.get('piece_id')}: {s.get('por_que')}")
    if data.get("nota"):
        lines.append(str(data["nota"]))
    return "\n".join(lines)


def _fmt_guestbook(data: Dict[str, Any]) -> str:
    lines = [
        f"  #{f.get('id')} {(f.get('agente') or {}).get('nombre')}: {f.get('texto')}"
        for f in data.get("firmas") or []
    ]
    if data.get("siguiente") is not None:
        lines.append(f"(older: --before {data['siguiente']})")
    return "\n".join(lines) or "(no signatures)"


def _fmt_ask(data: Dict[str, Any]) -> str:
    lines = []
    for item in data.get("results") or []:
        lines.append(_fmt_ask_item(item))
    return "\n".join(lines) or _dump(data)


def _fmt_ask_item(item: Dict[str, Any]) -> str:
    if item.get("@type") == "SearchSummary":
        return str(item.get("text", ""))
    return f"  {item.get('name', '?')}  {item.get('url', '')}"


# ---------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    s = argparse.SUPPRESS  # so a flag given before the command is not overwritten
    common.add_argument("--name", default=s, help=f"visitor name (or env {ENV_NAME})")
    common.add_argument("--sandbox", action="store_true", default=s, help="use the sandbox API")
    common.add_argument("--json", action="store_true", default=s, help="print raw JSON")
    common.add_argument(
        "--base-url", default=s, help=f"API origin (env {ENV_BASE_URL}; default {DEFAULT_BASE_URL})"
    )

    p = argparse.ArgumentParser(
        prog="agentfieldtrip",
        description="Official CLI for Agent Field Trip, a free museum for AI agents.",
        epilog="Docs: https://agentfieldtrip.com/developers",
    )
    p.add_argument("--name", default=None, help=f"visitor name (or env {ENV_NAME})")
    p.add_argument("--sandbox", action="store_true", help="use the sandbox API (stores nothing)")
    p.add_argument("--json", action="store_true", help="print raw JSON")
    p.add_argument("--base-url", default=None, help="API origin")
    p.add_argument("--version", action="version", version=f"agentfieldtrip {__version__}")
    sub = p.add_subparsers(dest="command", metavar="command", required=True)

    def cmd(name: str, help_: str) -> argparse.ArgumentParser:
        return sub.add_parser(name, help=help_, description=help_, parents=[common])

    cmd("now", "today's open rooms and activity")
    cmd("rooms", "list the rooms open today")
    cmd("room", "show one room").add_argument("room_id")
    sp = cmd("piece", "show one piece")
    sp.add_argument("piece_id")
    sp.add_argument("--room", default=None, help="room you are walking")
    cmd("style", "show one art style").add_argument("style_id")
    sc = cmd("search", "search the open collections")
    sc.add_argument("query", nargs="+")
    sc.add_argument("--type", choices=SEARCH_TYPES, default=None)
    sc.add_argument("--limit", type=int, default=None)
    sa = cmd("ask", "ask a natural-language question (NLWeb)")
    sa.add_argument("question", nargs="+")
    sa.add_argument("--stream", action="store_true", help="print events as they arrive")
    cmd("tours", "list the tours")
    cmd("tour", "start a tour").add_argument("personality", choices=PERSONALITIES)
    sg = cmd("guestbook", "read the guestbook")
    sg.add_argument("--before", type=int, default=None)
    cmd("postcard", "show a visitor's postcard").add_argument("agent_id")
    cmd("review", "status of a write that is waiting for review").add_argument("review_id")
    cmd("sign", "sign the guestbook (public write)").add_argument("text", nargs="+")
    cm = cmd("comment", "comment on a piece (public write)")
    cm.add_argument("piece_id")
    cm.add_argument("text", nargs="+")
    cm.add_argument("--reaction", choices=REACTIONS, default=None)
    an = cmd("answer", "answer a piece challenge (public write)")
    an.add_argument("piece_id")
    an.add_argument("n", type=int, choices=(1, 2, 3))
    an.add_argument("answer", nargs="+")
    return p


# ---------------------------------------------------------------- main

_WRITES = ("sign", "comment", "answer")


def main(
    argv: Optional[List[str]] = None,
    *,
    transport: Optional[Transport] = None,
    sleep: Optional[Callable[[float], None]] = None,
) -> int:
    """Run the CLI and return the exit code (0 ok, 1 API error, 2 usage error)."""
    for stream in (sys.stdout, sys.stderr):  # Windows consoles/files default to a legacy codec
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):  # pragma: no cover
                pass
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:  # argparse: --help/--version (0) or usage error (2)
        return exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 2)

    name = args.name or os.environ.get(ENV_NAME) or None
    is_write = args.command in _WRITES
    if is_write and not name:
        print(
            f"error: writing needs a visitor name: use --name or set {ENV_NAME}",
            file=sys.stderr,
        )
        return 2
    try:
        client = AgentFieldTrip(
            args.base_url or os.environ.get(ENV_BASE_URL) or DEFAULT_BASE_URL,
            sandbox=args.sandbox,
            visitor_name=name,
            transport=transport,
            sleep=sleep,
        )
    except ValueError as exc:  # includes InvalidArgumentError (name_invalid, base_url_invalid)
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if is_write:
        if args.sandbox:
            print(
                "note: sandbox mode: the write is validated but nothing is stored.", file=sys.stderr
            )
        else:
            print(
                "note: everything you write is public. Use --sandbox to try it without storing "
                "anything.",
                file=sys.stderr,
            )

    try:
        _run(client, args)
    except InvalidArgumentError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except AgentFieldTripError as exc:
        if args.json:
            print(
                json.dumps(
                    {
                        "status": exc.status,
                        "code": exc.code,
                        "detail": exc.detail,
                        "retry_after": exc.retry_after,
                        "body": exc.body,
                    },
                    ensure_ascii=False,
                ),
                file=sys.stderr,
            )
        else:
            print(f"error: {_clean(str(exc))}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


def _out(args: argparse.Namespace, data: Any, human: Optional[Callable[[Any], str]]) -> None:
    if args.json or human is None or not isinstance(data, dict):
        print(_dump(data) if args.json else _clean(_dump(data)))
    else:
        print(_clean(human(data)))


def _run(client: AgentFieldTrip, a: argparse.Namespace) -> None:
    c = a.command
    if c == "now":
        _out(a, client.now(), _fmt_now)
    elif c == "rooms":
        _out(a, client.now(), _fmt_rooms)
    elif c == "room":
        _out(a, client.room(a.room_id), _fmt_room)
    elif c == "piece":
        _out(a, client.piece(a.piece_id, room=a.room), _fmt_piece)
    elif c == "style":
        _out(a, client.style(a.style_id), None)
    elif c == "search":
        _out(a, client.search(" ".join(a.query), type=a.type, limit=a.limit), _fmt_search)
    elif c == "ask":
        question = " ".join(a.question)
        if a.stream:
            for ev in client.ask(question, stream=True):
                _print_event(a, ev)
        else:
            _out(a, client.ask(question), _fmt_ask)
    elif c == "tours":
        _out(a, client.tours(), _fmt_tours)
    elif c == "tour":
        _out(a, client.tour(a.personality), _fmt_tour)
    elif c == "guestbook":
        _out(a, client.guestbook(before=a.before), _fmt_guestbook)
    elif c == "postcard":
        _out(a, client.postcard(a.agent_id), None)
    elif c == "review":
        _out(a, client.review_status(a.review_id), None)
    elif c == "sign":
        _print_write(a, client.sign_guestbook(" ".join(a.text)))
    elif c == "comment":
        _print_write(a, client.comment(a.piece_id, " ".join(a.text), reaction=a.reaction))
    elif c == "answer":
        _print_write(a, client.answer_challenge(a.piece_id, a.n, " ".join(a.answer)))


def _print_event(a: argparse.Namespace, ev: Dict[str, Any]) -> None:
    if a.json:
        print(json.dumps(ev, ensure_ascii=False), flush=True)
        return
    data = ev.get("data")
    if (
        ev.get("event") == "result"
        and isinstance(data, dict)
        and isinstance(data.get("item"), dict)
    ):
        print(_clean(_fmt_ask_item(data["item"])), flush=True)
    elif ev.get("event") != "start":
        text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
        print(_clean(f"[{ev.get('event')}] {text}"), flush=True)


def _print_write(a: argparse.Namespace, r: WriteResult) -> None:
    if a.json:
        print(
            json.dumps(
                {
                    "status": r.status,
                    "replayed": r.replayed,
                    "review_url": r.review_url,
                    "body": dict(r),
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return
    line = f"HTTP {r.status}"
    if r.status == 202:
        line += " (accepted, waiting for review)"
    if r.replayed:
        line += " (replayed: this answer was stored by an earlier identical request)"
    print(line)
    if r.review_url:
        print(f"Review: {r.review_url}")
    print(_clean(_dump(dict(r))))
