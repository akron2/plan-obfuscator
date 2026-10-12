from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Case, Symbol
from ..parsing.types import ENTITY_CODES, TextOccurrence

TOKEN_SCHEME_VERSION = "1"
PREFIX_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


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
        self._cache: dict[tuple[str, str], Symbol] = {
            (symbol.entity_type, symbol.canonical_value): symbol for symbol in case.symbols
        }
        maximum = session.scalar(select(func.max(Symbol.sequence)).where(Symbol.case_id == case.id))
        self._next_sequence = int(maximum or 0) + 1

    def get_or_create(self, occurrence: TextOccurrence) -> Symbol:
        key = (occurrence.entity_type.value, occurrence.canonical)
        symbol = self._cache.get(key)
        if symbol is not None:
            symbol.last_used_at = datetime.now(UTC)
            symbol.confidence = max(symbol.confidence, occurrence.confidence)
            return symbol

        type_code = ENTITY_CODES[occurrence.entity_type]
        sequence = self._next_sequence
        self._next_sequence += 1
        symbol = Symbol(
            case_id=self.case.id,
            entity_type=occurrence.entity_type.value,
            canonical_value=occurrence.canonical,
            preferred_original=occurrence.original,
            marker=build_marker(self.case.token_prefix, type_code, sequence),
            sequence=sequence,
            provenance=occurrence.provenance,
            confidence=occurrence.confidence,
        )
        self.session.add(symbol)
        self.session.flush()
        self._cache[key] = symbol
        self.case.symbols.append(symbol) if symbol not in self.case.symbols else None
        return symbol
