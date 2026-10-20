import json
import os
import threading
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError

from services.schemas.speaking import (
    SpeakingContext, SpeakingCorrection, SpeakingEndReason, SpeakingEventRequest,
    SpeakingSession, SpeakingStatus, SpeakingSuggestion, SpeakingTurn, TargetUsage,
)
from services.speaking.matching import record_target_usage


DEFAULT_SPEAKING_PATH = Path(__file__).resolve().parents[2] / "storage" / "speaking_sessions.json"
MAX_TURNS = 40
MAX_TRANSCRIPT_CHARS = 30_000
MAX_CORRECTIONS = 8
MAX_SUGGESTIONS = 8


class SpeakingStoreError(RuntimeError):
    pass


class SpeakingConflictError(RuntimeError):
    pass


class SpeakingSessionStore:
    def __init__(self, path=DEFAULT_SPEAKING_PATH, *, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc), id_factory=lambda: uuid.uuid4().hex):
        self.path, self.clock, self.id_factory = Path(path), clock, id_factory
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write([])

    def create(self, *, user_id: str, provider: str, model: str, duration: int, context: SpeakingContext, goal: str | None) -> SpeakingSession:
        with self._lock:
            sessions = self._read()
            now = self._now()
            targets = [TargetUsage(**target.model_dump()) for target in context.target_chunks]
            session = SpeakingSession(
                id=self.id_factory(), user_id=user_id, language=context.language,
                level=context.level, mode=context.mode, provider=provider, model=model,
                requested_duration_seconds=duration, library_item_id=context.library_item_id,
                media_title_snapshot=context.media_title, goal=goal, started_at=now,
                target_chunks=context.target_chunks, support_words=context.support_words,
                target_usage=targets,
            )
            sessions.append(session)
            self._write(sessions)
            return session

    def get(self, user_id: str, session_id: str) -> SpeakingSession:
        with self._lock:
            for session in self._read():
                if session.id == session_id and session.user_id == user_id:
                    return session
        raise KeyError(session_id)

    def list_recent(self, user_id: str, limit: int = 10) -> list[SpeakingSession]:
        with self._lock:
            values = [item for item in self._read() if item.user_id == user_id]
        return sorted(values, key=lambda item: item.started_at, reverse=True)[:min(10, max(1, limit))]

    def append_event(self, user_id: str, session_id: str, event: SpeakingEventRequest) -> SpeakingSession:
        with self._lock:
            sessions = self._read()
            index, session = self._find(sessions, user_id, session_id)
            self._check_active(session, event.expected_event_count)
            turns, corrections, suggestions = list(session.turns), list(session.corrections), list(session.suggestions)
            usage = list(session.target_usage)
            learner, agent = session.learner_turn_count, session.agent_turn_count
            if event.type == "turn":
                if event.provider_turn_id and any(turn.provider_turn_id == event.provider_turn_id for turn in turns):
                    return session
                if len(turns) >= MAX_TURNS:
                    raise ValueError("Speaking transcript turn limit reached")
                text = " ".join((event.text or "").split())
                if sum(len(turn.text) for turn in turns) + len(text) > MAX_TRANSCRIPT_CHARS:
                    raise ValueError("Speaking transcript character limit reached")
                sequence = len(turns) + 1
                turns.append(SpeakingTurn(sequence=sequence, role=event.role, text=text, created_at=self._now(), provider_turn_id=event.provider_turn_id))
                if event.role == "learner":
                    learner += 1
                    usage = record_target_usage(usage, text, sequence)
                else:
                    agent += 1
            elif event.type == "correction":
                if len(corrections) >= MAX_CORRECTIONS:
                    raise ValueError("Speaking correction limit reached")
                corrections.append(event.correction)
            else:
                if len(suggestions) >= MAX_SUGGESTIONS:
                    raise ValueError("Speaking suggestion limit reached")
                suggestions.append(event.suggestion)
            updated = session.model_copy(update={
                "event_count": session.event_count + 1, "turns": turns,
                "learner_turn_count": learner, "agent_turn_count": agent,
                "corrections": corrections, "suggestions": suggestions,
                "target_usage": usage,
            })
            sessions[index] = updated
            self._write(sessions)
            return updated

    def finish(self, user_id: str, session_id: str, expected: int, status: SpeakingStatus, reason: SpeakingEndReason) -> SpeakingSession:
        with self._lock:
            sessions = self._read()
            index, session = self._find(sessions, user_id, session_id)
            self._check_active(session, expected)
            updated = session.model_copy(update={
                "event_count": session.event_count + 1, "status": status,
                "end_reason": reason, "ended_at": self._now(),
            })
            sessions[index] = updated
            self._write(sessions)
            return updated

    @staticmethod
    def _find(sessions, user_id, session_id):
        for index, session in enumerate(sessions):
            if session.id == session_id and session.user_id == user_id:
                return index, session
        raise KeyError(session_id)

    @staticmethod
    def _check_active(session, expected):
        if session.status != "active":
            raise SpeakingConflictError("Speaking session is no longer active")
        if session.event_count != expected:
            raise SpeakingConflictError("Speaking session state is stale")

    def _read(self):
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SpeakingStoreError("Malformed Speaking session storage") from exc
        if not isinstance(payload, dict) or payload.get("version") != 1 or not isinstance(payload.get("sessions"), list):
            raise SpeakingStoreError("Unsupported Speaking session storage")
        try:
            return [SpeakingSession.model_validate(value) for value in payload["sessions"]]
        except ValidationError as exc:
            raise SpeakingStoreError("Malformed Speaking session") from exc

    def _write(self, sessions):
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            temporary.write_text(json.dumps({"version": 1, "sessions": [item.model_dump(mode="json") for item in sessions]}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(temporary, self.path)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise SpeakingStoreError("Could not write Speaking session storage") from exc

    def _now(self):
        value = self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise SpeakingStoreError("Speaking clock must be timezone-aware")
        return value.astimezone(timezone.utc)
