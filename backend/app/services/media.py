"""Local file storage for message media. Swap for S3-compatible storage in production (same interface)."""

import mimetypes
import uuid
from datetime import UTC, datetime
from pathlib import Path

from app.core.config import settings


def _root() -> Path:
    root = Path(settings.MEDIA_ROOT)
    root.mkdir(parents=True, exist_ok=True)
    return root


def save(tenant_id: uuid.UUID, content: bytes, mime: str | None, filename: str | None = None) -> str:
    """Store bytes and return the relative path."""
    ext = Path(filename).suffix if filename and Path(filename).suffix else (mimetypes.guess_extension(mime or "") or ".bin")
    rel = Path(str(tenant_id)) / datetime.now(UTC).strftime("%Y/%m") / f"{uuid.uuid4().hex}{ext}"
    full = _root() / rel
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_bytes(content)
    return rel.as_posix()


def full_path(rel: str) -> Path:
    path = (_root() / rel).resolve()
    if _root().resolve() not in path.parents:
        raise ValueError("Invalid media path")
    return path


def read(rel: str) -> bytes:
    return full_path(rel).read_bytes()
