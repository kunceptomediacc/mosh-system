from __future__ import annotations

import hashlib
import re
from pathlib import PurePosixPath


ALLOWED_MEMORY_SOURCES = {"task", "event"}
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----", re.IGNORECASE),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{12,}", re.IGNORECASE),
    re.compile(r"\b(?:api[_-]?key|password|secret|token)\s*[:=]\s*\S+", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def validate_memory_candidate(summary: str, source_kind: str, source_reference_id: str) -> str:
    normalized = " ".join(summary.split()).strip()
    if not normalized or len(normalized) > 2000:
        raise ValueError("memory summary must be 1..2000 normalized characters")
    if source_kind not in ALLOWED_MEMORY_SOURCES:
        raise ValueError("memory source is not allowlisted")
    source_lower = source_reference_id.replace("/", "\\").lower()
    if "d:\\balot\\thor" in source_lower:
        raise ValueError("memory source is permanently excluded")
    if any(pattern.search(normalized) for pattern in SECRET_PATTERNS):
        raise ValueError("memory summary contains prohibited credential-shaped content")
    return normalized


def validate_procedure_ref(value: str) -> str:
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if not normalized.startswith("skills/") or path.is_absolute() or ".." in path.parts:
        raise ValueError("skill procedure must be a relative path beneath skills/")
    if len(normalized) > 400:
        raise ValueError("skill procedure reference is too long")
    return normalized
