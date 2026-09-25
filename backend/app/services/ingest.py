"""Single uploaded document indexing with short, explicit DB transactions."""

import hashlib
import logging
import math
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.chunking import ChunkConfig, chunk_sections
from app.llm.contracts import EmbeddingClient, ModelError
from app.models import DocumentBuild
from app.parsers.pdf import parse_pdf
from app.parsers.text import ParseError, parse_file
from app.repositories import chunks as repo

VECTOR_DIMENSIONS = 1536
_STORAGE_KEY = re.compile(r"objects/[0-9a-f]{32}\Z")
LOGGER = logging.getLogger("ragdesk.ingest")


class IngestError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class EmbeddingProfile:
    provider: str
    model: str
    dimensions: int = VECTOR_DIMENSIONS
    config_version: str = "raw-v1"
    chunk_size: int = 600
    overlap: int = 80
    batch_size: int = 16

    def __post_init__(self) -> None:
        ChunkConfig(self.chunk_size, self.overlap)
        if (
            self.provider not in {"fake", "openai"}
            or not self.model.strip()
            or not self.config_version.strip()
            or ":" in self.model
            or ":" in self.config_version
            or len(self.model) > 100
            or len(self.config_version) > 50
        ):
            raise ValueError("embedding identity is invalid")
        if type(self.dimensions) is not int or self.dimensions != VECTOR_DIMENSIONS:
            raise ValueError("embedding dimensions must match vector(1536)")
        if type(self.batch_size) is not int or not 1 <= self.batch_size <= 2048:
            raise ValueError("batch_size must be between 1 and 2048")
        if len(self.config_id) > 200:
            raise ValueError("embedding configuration ID is too long")

    @property
    def config_id(self) -> str:
        return f"{self.provider}:{self.model}:{self.dimensions}:{self.config_version}"

    @classmethod
    def fake(cls, **kwargs) -> "EmbeddingProfile":
        return cls(provider="fake", model="sha256-onehot-v1", **kwargs)


@dataclass(frozen=True, slots=True)
class IngestOutcome:
    build_id: UUID
    status: Literal["ready", "failed", "processing"]
    chunk_count: int
    model_config_id: str
    reused: bool = False
    error_code: str | None = None


def _validate_vectors(vectors: list[list[float]], count: int) -> None:
    if not isinstance(vectors, list) or len(vectors) != count:
        raise IngestError("EMBEDDING_COUNT_MISMATCH")
    for vector in vectors:
        if not isinstance(vector, list) or len(vector) != VECTOR_DIMENSIONS:
            raise IngestError("EMBEDDING_DIMENSION_MISMATCH")
        try:
            finite = all(
                type(value) in (int, float) and math.isfinite(value) for value in vector
            )
        except OverflowError:
            finite = False
        if not finite or not any(value != 0 for value in vector):
            raise IngestError("EMBEDDING_INVALID_VECTOR")


def _failure(
    factory: sessionmaker[Session], build_id: UUID, config_id: str, code: str
) -> IngestOutcome:
    with factory.begin() as session:
        build = session.get(DocumentBuild, build_id, with_for_update=True)
        if build is None:
            raise IngestError("BUILD_NOT_FOUND")
        if build.status == "processing":
            build.status = "failed"
            build.error_code = code
            build.error_message = code  # No provider text, source text or secret.
            build.finished_at = datetime.now(timezone.utc)
        count = repo.candidate_totals(session, build_id)[0]
    return IngestOutcome(build_id, "failed", count, config_id, error_code=code)


def ingest_document(
    factory: sessionmaker[Session],
    document_id: UUID,
    storage_dir: Path,
    client: EmbeddingClient,
    profile: EmbeddingProfile,
    build_id: UUID | None = None,
) -> IngestOutcome:
    """Index one private upload; caller must be a trusted local DB operator."""
    if (
        client.provider != profile.provider
        or client.model != profile.model
        or client.dimensions != profile.dimensions
    ):
        raise IngestError("EMBEDDING_CONFIG_MISMATCH")
    build_id = build_id or uuid4()
    with factory.begin() as session:
        document = repo.locked_document(session, document_id)
        if document is None or document.deleted_at is not None:
            raise IngestError("DOCUMENT_NOT_FOUND")
        existing = session.get(DocumentBuild, build_id)
        if existing is not None:
            if (
                existing.document_id != document_id
                or existing.model_config_id != profile.config_id
                or existing.chunking_config.get("chunk_size") != profile.chunk_size
                or existing.chunking_config.get("overlap") != profile.overlap
            ):
                raise IngestError("BUILD_CONFIG_MISMATCH")
            return IngestOutcome(
                build_id=build_id,
                status=existing.status,
                chunk_count=repo.candidate_totals(session, build_id)[0],
                model_config_id=profile.config_id,
                reused=True,
                error_code=existing.error_code,
            )
        if repo.processing_build_exists(session, document_id):
            raise IngestError("DOCUMENT_BUILD_IN_PROGRESS")
        storage_key = document.storage_key
        file_name = document.file_name
        file_hash = document.file_sha256
        kb_id = document.kb_id
        suffix = Path(file_name).suffix.lower()
        session.add(
            DocumentBuild(
                id=build_id,
                document_id=document_id,
                status="processing",
                parser_config={"version": "text-v1" if suffix != ".pdf" else "pdf-v1"},
                chunking_config={
                    "version": "character-v1",
                    "chunk_size": profile.chunk_size,
                    "overlap": profile.overlap,
                },
                model_config_id=profile.config_id,
                embedding_provider=profile.provider,
                embedding_model=profile.model,
                embedding_dimensions=profile.dimensions,
                config_version=profile.config_version,
            )
        )

    try:
        if suffix not in {".md", ".txt", ".pdf"}:
            raise IngestError("UNSUPPORTED_FORMAT")
        if not _STORAGE_KEY.fullmatch(storage_key):
            raise IngestError("INVALID_STORAGE_KEY")
        root = storage_dir.resolve()
        source_path = root / storage_key
        with tempfile.TemporaryDirectory(dir=root) as temporary:
            parsing_path = Path(temporary) / f"document{suffix}"
            shutil.copyfile(source_path, parsing_path)
            if hashlib.sha256(parsing_path.read_bytes()).hexdigest() != file_hash:
                raise IngestError("DOCUMENT_HASH_MISMATCH")
            if suffix == ".pdf":
                parsed = parse_pdf(parsing_path, document_id=str(document_id))
                if parsed.status != "complete":
                    raise IngestError("PARTIAL_PDF")
                sections = parsed.sections
            else:
                sections = parse_file(parsing_path, document_id=str(document_id))
        drafts = chunk_sections(
            sections, ChunkConfig(profile.chunk_size, profile.overlap)
        ).chunks
        if not drafts:
            raise IngestError("EMPTY_CHUNKS")
        with factory.begin() as session:
            build = session.get(DocumentBuild, build_id, with_for_update=True)
            if build is None or build.status != "processing":
                raise IngestError("BUILD_NOT_PROCESSING")
            build.expected_chunk_count = len(drafts)

        for start in range(0, len(drafts), profile.batch_size):
            batch = drafts[start : start + profile.batch_size]
            result = client.embed_documents([draft.text for draft in batch])
            _validate_vectors(result.vectors, len(batch))
            with factory.begin() as session:
                build = session.get(DocumentBuild, build_id, with_for_update=True)
                if build is None or build.status != "processing":
                    raise IngestError("BUILD_NOT_PROCESSING")
                repo.insert_candidates(session, build_id, batch, result.vectors)

        with factory.begin() as session:
            if repo.locked_kb(session, kb_id) is None:
                raise IngestError("KB_NOT_FOUND")
            document = repo.locked_document(session, document_id)
            build = session.get(DocumentBuild, build_id, with_for_update=True)
            if (
                document is None
                or document.deleted_at is not None
                or build is None
                or build.status != "processing"
            ):
                raise IngestError("BUILD_NOT_PUBLISHABLE")
            if repo.active_other_config_ids(session, kb_id, document_id) - {
                profile.config_id
            }:
                raise IngestError("INCOMPATIBLE_KB_INDEX")
            count, low, high, missing = repo.candidate_totals(session, build_id)
            if (
                count != build.expected_chunk_count
                or low != 0
                or high != count - 1
                or missing != 0
            ):
                raise IngestError("INCOMPLETE_BUILD")
            build.status = "ready"
            build.finished_at = datetime.now(timezone.utc)
            document.active_build_id = build_id
        return IngestOutcome(build_id, "ready", len(drafts), profile.config_id)
    except Exception as exc:
        code = (
            exc.code
            if isinstance(exc, (IngestError, ModelError, ParseError))
            else "INGEST_FAILED"
        )
        LOGGER.warning(
            "build_failed build_id=%s code=%s exception_type=%s constraint=%s",
            build_id,
            code,
            type(exc).__name__,
            getattr(
                getattr(getattr(exc, "orig", None), "diag", None),
                "constraint_name",
                None,
            )
            if isinstance(exc, IntegrityError)
            else None,
        )
        return _failure(factory, build_id, profile.config_id, code)
