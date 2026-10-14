from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import inspect, select, update
from sqlalchemy.orm import Session

from ..models import Case, Symbol
from ..parsing.types import ENTITY_CODES, TextOccurrence

TOKEN_SCHEME_VERSION = "1"
PREFIX_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


@dataclass(slots=True)
class SymbolEntry:
    id: str
    case_id: str
    entity_type: str
    canonical_value: str
    preferred_original: str
    marker: str
    sequence: int
    provenance: str
    confidence: float
    created_at: datetime
    last_used_at: datetime


def generate_case_prefix(length: int = 4) -> str:
    return "".join(secrets.choice(PREFIX_ALPHABET) for _ in range(length))


def token_checksum(prefix: str, type_code: str, sequence: int) -> str:
    payload = f"{prefix}:{type_code}:{sequence}:{TOKEN_SCHEME_VERSION}"
    return hashlib.blake2s(payload.encode("ascii"), digest_size=2).hexdigest()[:2].upper()


def build_marker(prefix: str, type_code: str, sequence: int) -> str:
    checksum = token_checksum(prefix, type_code, sequence)
    return f"OBF_{prefix}_{type_code}_{sequence:04d}_{checksum}"


class SymbolRegistry:
    def __init__(self, session: Session, case: Case) -> None:
        self.session = session
        self.case = case
        self._now = datetime.now(UTC)
        self._id_prefix = f"{uuid.uuid4().hex[:24]}-"
        self._cache: dict[tuple[str, str], SymbolEntry] = {}
        self._new_entries: list[SymbolEntry] = []
        self._touched_existing = False
        self._confidence_updates: dict[str, float] = {}

        rows = session.execute(
            select(
                Symbol.id,
                Symbol.entity_type,
                Symbol.canonical_value,
                Symbol.preferred_original,
                Symbol.marker,
                Symbol.sequence,
                Symbol.provenance,
                Symbol.confidence,
                Symbol.created_at,
                Symbol.last_used_at,
            ).where(Symbol.case_id == case.id)
        ).all()
        max_sequence = 0
        for row in rows:
            entry = SymbolEntry(
                id=row[0],
                case_id=case.id,
                entity_type=row[1],
                canonical_value=row[2],
                preferred_original=row[3],
                marker=row[4],
                sequence=row[5],
                provenance=row[6],
                confidence=row[7],
                created_at=row[8],
                last_used_at=row[9],
            )
            self._cache[(entry.entity_type, entry.canonical_value)] = entry
            if entry.sequence > max_sequence:
                max_sequence = entry.sequence
        self._next_sequence = max_sequence + 1

    def get_or_create(self, occurrence: TextOccurrence) -> SymbolEntry:
        key = (occurrence.entity_type.value, occurrence.canonical)
        symbol = self._cache.get(key)
        if symbol is not None:
            symbol.last_used_at = self._now
            self._touched_existing = True
            if occurrence.confidence > symbol.confidence:
                symbol.confidence = occurrence.confidence
                self._confidence_updates[symbol.id] = occurrence.confidence
            return symbol

        type_code = ENTITY_CODES[occurrence.entity_type]
        sequence = self._next_sequence
        self._next_sequence += 1
        symbol = SymbolEntry(
            id=f"{self._id_prefix}{sequence:08x}",
            case_id=self.case.id,
            entity_type=occurrence.entity_type.value,
            canonical_value=occurrence.canonical,
            preferred_original=occurrence.original,
            marker=build_marker(self.case.token_prefix, type_code, sequence),
            sequence=sequence,
            provenance=occurrence.provenance,
            confidence=occurrence.confidence,
            created_at=self._now,
            last_used_at=self._now,
        )
        self._cache[key] = symbol
        self._new_entries.append(symbol)
        return symbol

    def persist(self) -> None:
        now_iso = self._now.isoformat(sep=" ")
        if self._touched_existing:
            self.session.execute(
                update(Symbol)
                .where(Symbol.case_id == self.case.id)
                .values(last_used_at=self._now)
            )
            for symbol_id, confidence in self._confidence_updates.items():
                self.session.execute(
                    update(Symbol)
                    .where(Symbol.id == symbol_id)
                    .values(confidence=confidence)
                )
        if self._new_entries:
            raw_conn = self.session.connection().connection.dbapi_connection
            if raw_conn is not None:
                raw_conn.cursor().executemany(
                    "INSERT INTO symbols ("
                    "id, case_id, entity_type, canonical_value, preferred_original, "
                    "marker, sequence, provenance, confidence, created_at, last_used_at"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        (
                            entry.id,
                            entry.case_id,
                            entry.entity_type,
                            entry.canonical_value,
                            entry.preferred_original,
                            entry.marker,
                            entry.sequence,
                            entry.provenance,
                            entry.confidence,
                            now_iso,
                            now_iso,
                        )
                        for entry in self._new_entries
                    ],
                )
        if "symbols" in inspect(self.case).dict:
            self.session.expire(self.case, ["symbols"])
