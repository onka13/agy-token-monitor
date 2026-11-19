"""Incremental transcript parser and SQLite statistics store for Antigravity."""

from __future__ import annotations

import csv
import glob
import json
import os
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_BRAIN_DIR = Path.home() / ".gemini" / "antigravity" / "brain"
DEFAULT_DB_PATH = Path(__file__).resolve().parent / "data" / "history.db"

_USER_REQ_RE = re.compile(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", re.DOTALL)


def parse_iso_ts(ts: str | None) -> float | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def extract_prompt_preview(content: str | None, max_len: int = 80) -> str:
    if not content:
        return ""
    match = _USER_REQ_RE.search(content)
    text = match.group(1) if match else content
    text = " ".join(text.split())
    if len(text) > max_len:
        return text[: max_len - 1] + "…"
    return text


@dataclass
class StepRecord:
    conversation_id: str
    step_index: int
    turn_index: int
    created_at: str
    source: str
    step_type: str
    status: str
    model: str
    input_tokens: int
    cache_read_tokens: int
    output_tokens: int
    tool_calls: list[str]
    thinking_chars: int
    content_chars: int
    has_error: bool


@dataclass
class TurnSummary:
    conversation_id: str
    turn_index: int
    started_at: str
    ended_at: str
    duration_seconds: float
    prompt_preview: str
    model: str
    step_count: int = 0
    tool_call_count: int = 0
    error_count: int = 0
    input_tokens: int = 0
    cache_read_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.cache_read_tokens + self.output_tokens


@dataclass
class SessionSummary:
    conversation_id: str
    transcript_path: str
    alias: str = ""
    first_prompt: str = ""
    first_seen_at: str = ""
    last_seen_at: str = ""
    last_model: str = ""
    total_turns: int = 0
    total_steps: int = 0
    total_tool_calls: int = 0
    total_errors: int = 0
    total_input_tokens: int = 0
    total_cache_read_tokens: int = 0
    total_output_tokens: int = 0
    turns: list[TurnSummary] = field(default_factory=list)

    @property
    def total_tokens(self) -> int:
        return (
            self.total_input_tokens
            + self.total_cache_read_tokens
            + self.total_output_tokens
        )

    @property
    def cache_hit_ratio(self) -> float:
        denom = self.total_input_tokens + self.total_cache_read_tokens
        if denom == 0:
            return 0.0
        return (self.total_cache_read_tokens / denom) * 100.0


class TokenHistoryStore:
    """SQLite-backed store for all Antigravity session, turn, and step statistics."""

    def __init__(self, db_path: Path = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        cur = self._conn.cursor()
        cur.executescript(
            """
            PRAGMA journal_mode=WAL;

            CREATE TABLE IF NOT EXISTS sessions (
                conversation_id TEXT PRIMARY KEY,
                transcript_path TEXT NOT NULL,
                alias TEXT NOT NULL DEFAULT '',
                first_prompt TEXT NOT NULL DEFAULT '',
                first_seen_at TEXT NOT NULL DEFAULT '',
                last_seen_at TEXT NOT NULL DEFAULT '',
                last_model TEXT NOT NULL DEFAULT '',
                total_turns INTEGER NOT NULL DEFAULT 0,
                total_steps INTEGER NOT NULL DEFAULT 0,
                total_tool_calls INTEGER NOT NULL DEFAULT 0,
                total_errors INTEGER NOT NULL DEFAULT 0,
                total_input_tokens INTEGER NOT NULL DEFAULT 0,
                total_cache_read_tokens INTEGER NOT NULL DEFAULT 0,
                total_output_tokens INTEGER NOT NULL DEFAULT 0,
                total_tokens INTEGER NOT NULL DEFAULT 0,
                file_mtime REAL NOT NULL DEFAULT 0,
                file_size INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS turns (
                conversation_id TEXT NOT NULL,
                turn_index INTEGER NOT NULL,
                started_at TEXT NOT NULL DEFAULT '',
                ended_at TEXT NOT NULL DEFAULT '',
                duration_seconds REAL NOT NULL DEFAULT 0,
                prompt_preview TEXT NOT NULL DEFAULT '',
                model TEXT NOT NULL DEFAULT '',
                step_count INTEGER NOT NULL DEFAULT 0,
                tool_call_count INTEGER NOT NULL DEFAULT 0,
                error_count INTEGER NOT NULL DEFAULT 0,
                input_tokens INTEGER NOT NULL DEFAULT 0,
                cache_read_tokens INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0,
                total_tokens INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (conversation_id, turn_index)
            );

            CREATE TABLE IF NOT EXISTS steps (
                conversation_id TEXT NOT NULL,
                step_index INTEGER NOT NULL,
                turn_index INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT '',
                step_type TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT '',
                model TEXT NOT NULL DEFAULT '',
                input_tokens INTEGER NOT NULL DEFAULT 0,
                cache_read_tokens INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0,
                tool_calls_json TEXT NOT NULL DEFAULT '[]',
                tool_call_count INTEGER NOT NULL DEFAULT 0,
                thinking_chars INTEGER NOT NULL DEFAULT 0,
                content_chars INTEGER NOT NULL DEFAULT 0,
                has_error INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (conversation_id, step_index)
            );
            """
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def set_alias(self, conversation_id: str, alias: str) -> None:
        self._conn.execute(
            "UPDATE sessions SET alias = ? WHERE conversation_id = ?",
            (alias.strip(), conversation_id),
        )
        self._conn.commit()

    def sync_all_transcripts(self, brain_dir: Path = DEFAULT_BRAIN_DIR) -> list[str]:
        """Scan brain_dir for transcript.jsonl files and sync any updated ones."""
        pattern = str(brain_dir / "*" / ".system_generated" / "logs" / "transcript.jsonl")
        updated_ids: list[str] = []
        for path_str in glob.glob(pattern):
            path = Path(path_str)
            conv_id = path.parts[-4]
            try:
                stat = path.stat()
            except OSError:
                continue

            row = self._conn.execute(
                "SELECT file_mtime, file_size FROM sessions WHERE conversation_id = ?",
                (conv_id,),
            ).fetchone()

            if row and row["file_mtime"] == stat.st_mtime and row["file_size"] == stat.st_size:
                continue

            self._ingest_transcript(conv_id, path, stat.st_mtime, stat.st_size)
            updated_ids.append(conv_id)
        return updated_ids

    def _ingest_transcript(
        self, conv_id: str, path: Path, mtime: float, size: int
    ) -> None:
        steps: list[StepRecord] = []
        turns_map: dict[int, TurnSummary] = {}
        current_turn_idx = 0

        try:
            with path.open("r", encoding="utf-8", errors="replace") as f:
                for raw_line in f:
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        obj: dict[str, Any] = json.loads(line)
                    except json.JSONDecodeError:
                        # File might be mid-write; skip incomplete trailing line.
                        continue

                    step_index = int(obj.get("step_index", len(steps)))
                    source = str(obj.get("source", ""))
                    step_type = str(obj.get("type", ""))
                    status = str(obj.get("status", ""))
                    created_at = str(obj.get("created_at", ""))
                    model = str(obj.get("model", ""))
                    input_tok = int(obj.get("input_tokens") or 0)
                    cache_tok = int(obj.get("cache_read_tokens") or 0)
                    output_tok = int(obj.get("output_tokens") or 0)
                    tool_calls_raw = obj.get("tool_calls") or []
                    tool_names = [
                        str(tc.get("name", "")) for tc in tool_calls_raw if isinstance(tc, dict)
                    ]
                    thinking_chars = len(str(obj.get("thinking") or ""))
                    content_chars = len(str(obj.get("content") or ""))
                    has_error = status == "ERROR" or bool(obj.get("error"))

                    if source == "USER_EXPLICIT" and step_type == "USER_INPUT":
                        current_turn_idx += 1
                        prompt_preview = extract_prompt_preview(obj.get("content"))
                        turns_map[current_turn_idx] = TurnSummary(
                            conversation_id=conv_id,
                            turn_index=current_turn_idx,
                            started_at=created_at,
                            ended_at=created_at,
                            duration_seconds=0.0,
                            prompt_preview=prompt_preview,
                            model="",
                        )

                    if current_turn_idx == 0:
                        current_turn_idx = 1
                        turns_map[1] = TurnSummary(
                            conversation_id=conv_id,
                            turn_index=1,
                            started_at=created_at,
                            ended_at=created_at,
                            duration_seconds=0.0,
                            prompt_preview="(Sistem / Başlangıç)",
                            model="",
                        )

                    turn = turns_map[current_turn_idx]
                    if created_at:
                        turn.ended_at = created_at
                        t0 = parse_iso_ts(turn.started_at)
                        t1 = parse_iso_ts(turn.ended_at)
                        if t0 is not None and t1 is not None and t1 >= t0:
                            turn.duration_seconds = round(t1 - t0, 2)

                    if step_type == "PLANNER_RESPONSE":
                        turn.step_count += 1
                        turn.tool_call_count += len(tool_names)
                        turn.input_tokens += input_tok
                        turn.cache_read_tokens += cache_tok
                        turn.output_tokens += output_tok
                        if model:
                            turn.model = model

                    if has_error:
                        turn.error_count += 1

                    steps.append(
                        StepRecord(
                            conversation_id=conv_id,
                            step_index=step_index,
                            turn_index=current_turn_idx,
                            created_at=created_at,
                            source=source,
                            step_type=step_type,
                            status=status,
                            model=model,
                            input_tokens=input_tok,
                            cache_read_tokens=cache_tok,
                            output_tokens=output_tok,
                            tool_calls=tool_names,
                            thinking_chars=thinking_chars,
                            content_chars=content_chars,
                            has_error=has_error,
                        )
                    )
        except OSError:
            return

        turns_list = [turns_map[k] for k in sorted(turns_map.keys())]
        first_prompt = next((t.prompt_preview for t in turns_list if t.prompt_preview), "")
        first_seen = steps[0].created_at if steps else ""
        last_seen = steps[-1].created_at if steps else ""
        last_model = next((s.model for s in reversed(steps) if s.model), "")

        total_turns = len(turns_list)
        total_steps = sum(t.step_count for t in turns_list)
        total_tool_calls = sum(t.tool_call_count for t in turns_list)
        total_errors = sum(t.error_count for t in turns_list)
        total_in = sum(t.input_tokens for t in turns_list)
        total_cache = sum(t.cache_read_tokens for t in turns_list)
        total_out = sum(t.output_tokens for t in turns_list)
        total_tok = total_in + total_cache + total_out

        existing = self._conn.execute(
            "SELECT alias FROM sessions WHERE conversation_id = ?", (conv_id,)
        ).fetchone()
        alias = existing["alias"] if existing else ""

        with self._conn:
            self._conn.execute(
                """
                INSERT INTO sessions (
                    conversation_id, transcript_path, alias, first_prompt,
                    first_seen_at, last_seen_at, last_model,
                    total_turns, total_steps, total_tool_calls, total_errors,
                    total_input_tokens, total_cache_read_tokens, total_output_tokens,
                    total_tokens, file_mtime, file_size
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(conversation_id) DO UPDATE SET
                    transcript_path=excluded.transcript_path,
                    first_prompt=excluded.first_prompt,
                    first_seen_at=excluded.first_seen_at,
                    last_seen_at=excluded.last_seen_at,
                    last_model=excluded.last_model,
                    total_turns=excluded.total_turns,
                    total_steps=excluded.total_steps,
                    total_tool_calls=excluded.total_tool_calls,
                    total_errors=excluded.total_errors,
                    total_input_tokens=excluded.total_input_tokens,
                    total_cache_read_tokens=excluded.total_cache_read_tokens,
                    total_output_tokens=excluded.total_output_tokens,
                    total_tokens=excluded.total_tokens,
                    file_mtime=excluded.file_mtime,
                    file_size=excluded.file_size
                """,
                (
                    conv_id,
                    str(path),
                    alias,
                    first_prompt,
                    first_seen,
                    last_seen,
                    last_model,
                    total_turns,
                    total_steps,
                    total_tool_calls,
                    total_errors,
                    total_in,
                    total_cache,
                    total_out,
                    total_tok,
                    mtime,
                    size,
                ),
            )

            self._conn.executemany(
                """
                INSERT INTO turns (
                    conversation_id, turn_index, started_at, ended_at, duration_seconds,
                    prompt_preview, model, step_count, tool_call_count, error_count,
                    input_tokens, cache_read_tokens, output_tokens, total_tokens
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(conversation_id, turn_index) DO UPDATE SET
                    started_at=excluded.started_at,
                    ended_at=excluded.ended_at,
                    duration_seconds=excluded.duration_seconds,
                    prompt_preview=excluded.prompt_preview,
                    model=excluded.model,
                    step_count=excluded.step_count,
                    tool_call_count=excluded.tool_call_count,
                    error_count=excluded.error_count,
                    input_tokens=excluded.input_tokens,
                    cache_read_tokens=excluded.cache_read_tokens,
                    output_tokens=excluded.output_tokens,
                    total_tokens=excluded.total_tokens
                """,
                [
                    (
                        t.conversation_id,
                        t.turn_index,
                        t.started_at,
                        t.ended_at,
                        t.duration_seconds,
                        t.prompt_preview,
                        t.model,
                        t.step_count,
                        t.tool_call_count,
                        t.error_count,
                        t.input_tokens,
                        t.cache_read_tokens,
                        t.output_tokens,
                        t.total_tokens,
                    )
                    for t in turns_list
                ],
            )

            self._conn.executemany(
                """
                INSERT INTO steps (
                    conversation_id, step_index, turn_index, created_at, source,
                    step_type, status, model, input_tokens, cache_read_tokens,
                    output_tokens, tool_calls_json, tool_call_count,
                    thinking_chars, content_chars, has_error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(conversation_id, step_index) DO UPDATE SET
                    turn_index=excluded.turn_index,
                    created_at=excluded.created_at,
                    source=excluded.source,
                    step_type=excluded.step_type,
                    status=excluded.status,
                    model=excluded.model,
                    input_tokens=excluded.input_tokens,
                    cache_read_tokens=excluded.cache_read_tokens,
                    output_tokens=excluded.output_tokens,
                    tool_calls_json=excluded.tool_calls_json,
                    tool_call_count=excluded.tool_call_count,
                    thinking_chars=excluded.thinking_chars,
                    content_chars=excluded.content_chars,
                    has_error=excluded.has_error
                """,
                [
                    (
                        s.conversation_id,
                        s.step_index,
                        s.turn_index,
                        s.created_at,
                        s.source,
                        s.step_type,
                        s.status,
                        s.model,
                        s.input_tokens,
                        s.cache_read_tokens,
                        s.output_tokens,
                        json.dumps(s.tool_calls, ensure_ascii=False),
                        len(s.tool_calls),
                        s.thinking_chars,
                        s.content_chars,
                        1 if s.has_error else 0,
                    )
                    for s in steps
                ],
            )

    def list_sessions(self) -> list[SessionSummary]:
        rows = self._conn.execute(
            """
            SELECT * FROM sessions
            ORDER BY last_seen_at DESC, file_mtime DESC
            """
        ).fetchall()
        return [
            SessionSummary(
                conversation_id=r["conversation_id"],
                transcript_path=r["transcript_path"],
                alias=r["alias"],
                first_prompt=r["first_prompt"],
                first_seen_at=r["first_seen_at"],
                last_seen_at=r["last_seen_at"],
                last_model=r["last_model"],
                total_turns=r["total_turns"],
                total_steps=r["total_steps"],
                total_tool_calls=r["total_tool_calls"],
                total_errors=r["total_errors"],
                total_input_tokens=r["total_input_tokens"],
                total_cache_read_tokens=r["total_cache_read_tokens"],
                total_output_tokens=r["total_output_tokens"],
            )
            for r in rows
        ]

    def get_session(self, conversation_id: str) -> SessionSummary | None:
        r = self._conn.execute(
            "SELECT * FROM sessions WHERE conversation_id = ?", (conversation_id,)
        ).fetchone()
        if not r:
            return None

        turn_rows = self._conn.execute(
            """
            SELECT * FROM turns
            WHERE conversation_id = ?
            ORDER BY turn_index ASC
            """,
            (conversation_id,),
        ).fetchall()

        turns = [
            TurnSummary(
                conversation_id=tr["conversation_id"],
                turn_index=tr["turn_index"],
                started_at=tr["started_at"],
                ended_at=tr["ended_at"],
                duration_seconds=tr["duration_seconds"],
                prompt_preview=tr["prompt_preview"],
                model=tr["model"],
                step_count=tr["step_count"],
                tool_call_count=tr["tool_call_count"],
                error_count=tr["error_count"],
                input_tokens=tr["input_tokens"],
                cache_read_tokens=tr["cache_read_tokens"],
                output_tokens=tr["output_tokens"],
            )
            for tr in turn_rows
        ]

        return SessionSummary(
            conversation_id=r["conversation_id"],
            transcript_path=r["transcript_path"],
            alias=r["alias"],
            first_prompt=r["first_prompt"],
            first_seen_at=r["first_seen_at"],
            last_seen_at=r["last_seen_at"],
            last_model=r["last_model"],
            total_turns=r["total_turns"],
            total_steps=r["total_steps"],
            total_tool_calls=r["total_tool_calls"],
            total_errors=r["total_errors"],
            total_input_tokens=r["total_input_tokens"],
            total_cache_read_tokens=r["total_cache_read_tokens"],
            total_output_tokens=r["total_output_tokens"],
            turns=turns,
        )

    def get_global_totals(self) -> dict[str, int]:
        r = self._conn.execute(
            """
            SELECT
                COUNT(*) AS session_count,
                COALESCE(SUM(total_turns), 0) AS turns,
                COALESCE(SUM(total_steps), 0) AS steps,
                COALESCE(SUM(total_input_tokens), 0) AS input_tokens,
                COALESCE(SUM(total_cache_read_tokens), 0) AS cache_read_tokens,
                COALESCE(SUM(total_output_tokens), 0) AS output_tokens,
                COALESCE(SUM(total_tokens), 0) AS total_tokens
            FROM sessions
            """
        ).fetchone()
        return dict(r) if r else {}

    def export_turns_csv(self, output_path: Path) -> int:
        rows = self._conn.execute(
            """
            SELECT
                t.conversation_id,
                s.alias,
                t.turn_index,
                t.started_at,
                t.ended_at,
                t.duration_seconds,
                t.model,
                t.step_count,
                t.tool_call_count,
                t.error_count,
                t.input_tokens,
                t.cache_read_tokens,
                t.output_tokens,
                t.total_tokens,
                t.prompt_preview
            FROM turns t
            JOIN sessions s ON s.conversation_id = t.conversation_id
            ORDER BY t.started_at DESC, t.turn_index DESC
            """
        ).fetchall()

        with Path(output_path).open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "conversation_id",
                    "alias",
                    "turn_index",
                    "started_at",
                    "ended_at",
                    "duration_seconds",
                    "model",
                    "step_count",
                    "tool_call_count",
                    "error_count",
                    "input_tokens",
                    "cache_read_tokens",
                    "output_tokens",
                    "total_tokens",
                    "prompt_preview",
                ]
            )
            for r in rows:
                writer.writerow(list(r))
        return len(rows)
