"""Private original-file storage and document access rules."""

import hashlib
import os
import re
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import UploadFile
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Document
from app.repositories import documents as repo
from app.services.ingestion_jobs import enqueue
from app.services.knowledge_bases import NotFound, require_kb_admin, require_kb_member

MAX_FILE_BYTES = 10 * 1024 * 1024
_READ_BYTES = 64 * 1024
_PDF_HEADER = re.compile(rb"%PDF-(?:1\.[0-7]|2\.0)\b")
_STORAGE_KEY = re.compile(r"objects/[0-9a-f]{32}\Z")


class DocumentError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status = status
        self.code = code
        self.message = message


def _filename(upload: UploadFile) -> tuple[str, str]:
    name = upload.filename
    if (
        not name
        or len(name) > 255
        or name in {".", ".."}
        or "/" in name
        or "\\" in name
        or any(ord(char) < 32 or ord(char) == 127 for char in name)
    ):
        raise DocumentError(400, "INVALID_FILENAME", "Invalid file name")
    suffix = Path(name).suffix.lower()
    if suffix not in {".md", ".txt", ".pdf"}:
        raise DocumentError(400, "UNSUPPORTED_FILE", "Unsupported file type")
    return name, suffix


def _check_format(path: Path, suffix: str) -> None:
    data = path.read_bytes()  # Bounded to 10 MiB by the upload loop.
    if suffix == ".pdf":
        if (
            not _PDF_HEADER.match(data[:16])
            or b"startxref" not in data
            or b"%%EOF" not in data[-1024:]
        ):
            raise DocumentError(400, "INVALID_FILE", "Invalid PDF structure")
        return
    try:
        decoded = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DocumentError(400, "INVALID_FILE", "Expected UTF-8 text") from exc
    if (
        data.startswith((b"%PDF-", b"\x89PNG", b"GIF87a", b"GIF89a", b"PK\x03\x04"))
        or any(ord(char) < 32 and char not in "\t\n\r" for char in decoded)
        or "\ufffd" in decoded
    ):
        raise DocumentError(400, "INVALID_FILE", "Invalid text file content")


def _same_bytes(left: Path, right: Path) -> bool:
    if left.stat().st_size != right.stat().st_size:
        return False
    with left.open("rb") as a, right.open("rb") as b:
        while piece := a.read(_READ_BYTES):
            if piece != b.read(len(piece)):
                return False
    return True


def _private_path(root: Path, storage_key: str) -> Path:
    if not _STORAGE_KEY.fullmatch(storage_key):
        raise RuntimeError("Invalid stored document key")
    return root / storage_key


def _existing_id(
    session: Session, kb_id: UUID, sha256: str, candidate: Path, root: Path
):
    existing = repo.by_hash(session, kb_id, sha256)
    if existing is None:
        return None
    if not _same_bytes(candidate, _private_path(root, existing.storage_key)):
        raise DocumentError(
            409, "HASH_COLLISION", "File hash conflicts with existing file"
        )
    return existing.id


def upload(
    session: Session,
    user_id: UUID,
    kb_id: UUID,
    file: UploadFile,
    storage_dir: Path,
    profile,
):
    require_kb_admin(session, user_id, kb_id)
    filename, suffix = _filename(file)
    root = storage_dir.resolve()
    staging = root / "staging"
    objects = root / "objects"
    for directory in (root, staging, objects):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name == "posix":
            directory.chmod(0o700)
    temporary = staging / uuid4().hex
    final: Path | None = None
    committed = False
    try:
        digest = hashlib.sha256()
        size = 0
        with temporary.open("xb") as output:
            if os.name == "posix":
                temporary.chmod(0o600)
            while part := file.file.read(_READ_BYTES):
                size += len(part)
                if size > MAX_FILE_BYTES:
                    raise DocumentError(413, "FILE_TOO_LARGE", "File exceeds 10 MiB")
                digest.update(part)
                output.write(part)
        if size == 0:
            raise DocumentError(400, "EMPTY_FILE", "File is empty")
        _check_format(temporary, suffix)
        sha256 = digest.hexdigest()
        existing_id = _existing_id(session, kb_id, sha256, temporary, root)
        if existing_id is not None:
            job = enqueue(
                session, user_id, kb_id, existing_id, profile, reuse_latest=True
            )
            session.commit()
            return existing_id, job

        storage_key = f"objects/{uuid4().hex}"
        final = _private_path(root, storage_key)
        os.replace(temporary, final)
        document = Document(
            id=uuid4(),
            kb_id=kb_id,
            file_name=filename,
            file_sha256=sha256,
            storage_key=storage_key,
        )
        session.add(document)
        try:
            session.flush()
            job = enqueue(session, user_id, kb_id, document.id, profile)
            session.commit()
        except IntegrityError:
            session.rollback()
            existing_id = _existing_id(session, kb_id, sha256, final, root)
            if existing_id is not None:
                job = enqueue(
                    session, user_id, kb_id, existing_id, profile, reuse_latest=True
                )
                session.commit()
                return existing_id, job
            raise
        committed = True
        return document.id, job
    except Exception:
        session.rollback()
        raise
    finally:
        temporary.unlink(missing_ok=True)
        if final is not None and not committed:
            final.unlink(missing_ok=True)


def list_documents(
    session: Session, user_id: UUID, kb_id: UUID, limit: int, offset: int
):
    require_kb_member(session, user_id, kb_id)
    return repo.page(session, kb_id, limit, offset)


def get_document(session: Session, user_id: UUID, kb_id: UUID, document_id: UUID):
    require_kb_member(session, user_id, kb_id)
    document = repo.by_id(session, kb_id, document_id)
    if document is None:
        raise NotFound()
    return document


def original_path(document: Document, storage_dir: Path) -> Path:
    path = _private_path(storage_dir.resolve(), document.storage_key)
    if not path.is_file():
        raise RuntimeError("Stored document is missing")
    return path
