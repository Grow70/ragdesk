"""Document lifecycle against isolated PostgreSQL; no real model calls."""

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker
from test_documents import _upload
from test_documents import document_env as document_env
from test_ingestion_jobs import job_env as job_env
from test_ingestion_jobs import upload

from app.llm.contracts import ModelError
from app.llm.fake import FakeEmbeddingClient
from app.models import Chunk, Document, DocumentBuild, IngestionJob
from app.services import bm25, documents, retrieval
from app.services import ingestion_jobs as jobs
from app.services.ingest import EmbeddingProfile, IngestError, ingest_document

CAN_CLEANUP = {os.open, os.stat, os.unlink}.issubset(os.supports_dir_fd) and hasattr(
    os, "O_NOFOLLOW"
)
REMOVED = "removed" if CAN_CLEANUP else "pending"
MISSING = "missing" if CAN_CLEANUP else "pending"


def _url(env, uploaded):
    return f"/knowledge-bases/{env[4]}/documents/{uploaded['document_id']}"


def _work(env, **kwargs):
    return jobs.process_one(
        sessionmaker(env[1]),
        env[2],
        lambda p: FakeEmbeddingClient(),
        heartbeat_interval=None,
        **kwargs,
    )


def _results(env):
    factory, user, kb = sessionmaker(env[1]), env[3]["carol"], UUID(env[4])
    return (
        retrieval.search(
            factory, user, kb, "limit", 5, EmbeddingProfile.fake(), FakeEmbeddingClient
        ),
        bm25.search(factory, user, kb, "limit"),
    )


def _source(env, item):
    return (
        f"/knowledge-bases/{env[4]}/sources/{item.document_id}/"
        f"{item.build_id}/{item.chunk_id}"
    )


def test_delete_hides_every_read_and_allows_new_upload(job_env):
    env = job_env
    client, engine, storage, _, a, b, alice, bob, carol = env
    uploaded = upload(env)
    assert _work(env)["status"] == "succeeded"
    vector, lexical = _results(env)
    assert vector and lexical
    source = _source(env, vector[0])
    path = _url(env, uploaded)
    with Session(engine) as session:
        key = session.get(Document, UUID(uploaded["document_id"])).storage_key
    assert client.delete(path, headers=carol).status_code == 403
    assert client.delete(path, headers=bob).status_code == 404
    assert client.delete(path.replace(a, b), headers=bob).status_code == 404
    assert client.post(path + "/rebuild", headers=carol).status_code == 403
    assert client.post(path + "/rebuild", headers=bob).status_code == 404
    assert client.post(path.replace(a, b) + "/rebuild", headers=bob).status_code == 404
    deleted = client.delete(path, headers=alice)
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["status"] == "deleted"
    assert deleted.json()["cleanup_status"] == REMOVED
    assert (storage / key).exists() is (not CAN_CLEANUP)
    assert _results(env) == ([], [])
    for protected in (path, path + "/raw", source, uploaded["status_url"]):
        assert client.get(protected, headers=alice).status_code == 404
    assert (
        client.get(f"/knowledge-bases/{a}/documents", headers=carol).json()["total"]
        == 0
    )
    assert client.post(path + "/rebuild", headers=alice).status_code == 404
    assert client.delete(path, headers=alice).json()["cleanup_status"] == MISSING
    replacement = upload(env)
    assert replacement["document_id"] != uploaded["document_id"]
    with Session(engine) as session:
        old = session.get(Document, UUID(uploaded["document_id"]))
        assert old.deleted_at and old.active_build_id is None
        assert (
            session.get(Document, UUID(replacement["document_id"])).storage_key != key
        )


def test_delete_cancels_queued_task_and_rolls_back_before_file_cleanup(
    job_env, monkeypatch
):
    env = job_env
    uploaded = upload(env)
    path = _url(env, uploaded)
    with Session(env[1]) as session:
        key = session.get(Document, UUID(uploaded["document_id"])).storage_key
    with Session(env[1]) as session:

        def fail_commit():
            raise RuntimeError("injected commit failure")

        monkeypatch.setattr(session, "commit", fail_commit)
        with pytest.raises(RuntimeError, match="injected"):
            documents.delete_document(
                session,
                env[3]["alice"],
                UUID(env[4]),
                UUID(uploaded["document_id"]),
                env[2],
            )
    assert (env[2] / key).exists()
    with Session(env[1]) as session:
        assert session.get(Document, UUID(uploaded["document_id"])).deleted_at is None
        assert session.get(IngestionJob, UUID(uploaded["job_id"])).status == "queued"
    assert env[0].delete(path, headers=env[6]).status_code == 200
    with Session(env[1]) as session:
        job = session.get(IngestionJob, UUID(uploaded["job_id"]))
        assert job.status == "failed" and job.error_code == "DOCUMENT_DELETED"
        assert job.run_token is None and job.lease_expires_at is None
    assert _work(env) is None


@pytest.mark.parametrize("pause_at", ["after_embedding", "before_publish"])
def test_delete_races_with_running_worker_without_resurrection(job_env, pause_at):
    env = job_env
    uploaded = upload(env)
    assert _work(env)["status"] == "succeeded"
    path = _url(env, uploaded)
    rebuilding = env[0].post(path + "/rebuild", headers=env[6]).json()
    entered, release = Event(), Event()

    def hook(stage):
        if stage == pause_at:
            entered.set()
            assert release.wait(10), "test must release worker"

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_work, env, fault_hook=hook)
        try:
            assert entered.wait(10)
            assert env[0].delete(path, headers=env[6]).status_code == 200
        finally:
            release.set()
        assert future.result(timeout=10)["status"] == "lease_lost"
    assert _results(env) == ([], [])
    with Session(env[1]) as session:
        doc = session.get(Document, UUID(uploaded["document_id"]))
        job = session.get(IngestionJob, UUID(rebuilding["job_id"]))
        assert doc.deleted_at and doc.active_build_id is None
        assert job.status == "failed" and job.error_code == "DOCUMENT_DELETED"
        assert session.get(DocumentBuild, job.build_id).status == "failed"
    assert _work(env) is None


def test_rebuild_keeps_old_until_success_then_expires_exact_source(job_env):
    env = job_env
    uploaded = upload(env)
    _work(env)
    old = _results(env)[0][0]
    source = _source(env, old)
    path = _url(env, uploaded)
    first = env[0].post(path + "/rebuild", headers=env[6])
    repeated = env[0].post(path + "/ingestions", headers=env[6])
    assert first.status_code == repeated.status_code == 202
    assert first.json()["job_id"] == repeated.json()["job_id"] != uploaded["job_id"]

    def check_old(stage):
        if stage == "before_publish":
            assert {c.build_id for lane in _results(env) for c in lane} == {
                old.build_id
            }
            assert env[0].get(source, headers=env[8]).status_code == 200

    outcome = _work(env, fault_hook=check_old)
    assert outcome["status"] == "succeeded"
    new_id = UUID(outcome["build_id"])
    assert new_id != old.build_id
    assert {c.build_id for lane in _results(env) for c in lane} == {new_id}
    expired = env[0].get(source, headers=env[8])
    assert (
        expired.status_code == 410
        and expired.json()["error"]["code"] == "SOURCE_EXPIRED"
    )
    assert old.text not in expired.text
    assert env[0].get(source, headers=env[7]).status_code == 404
    wrong_chain = source.replace(str(old.build_id), str(new_id))
    assert env[0].get(wrong_chain, headers=env[8]).status_code == 404
    assert (
        env[0]
        .get(source.replace(str(old.chunk_id), str(uuid4())), headers=env[8])
        .status_code
        == 404
    )
    with Session(env[1]) as session:
        assert (
            session.scalar(
                select(func.count()).select_from(Chunk).where(Chunk.build_id == new_id)
            )
            == 1
        )


def test_failed_rebuild_and_modified_original_preserve_old_index(job_env):
    env = job_env
    uploaded = upload(env)
    _work(env)
    old = _results(env)[0][0]
    path = _url(env, uploaded)
    env[0].post(path + "/rebuild", headers=env[6])

    class Failing(FakeEmbeddingClient):
        def embed_documents(self, texts):
            raise ModelError("MODEL_AUTH_FAILED")

    failed = jobs.process_one(
        sessionmaker(env[1]), env[2], lambda p: Failing(), heartbeat_interval=None
    )
    assert failed["status"] == "failed" and failed["error_code"] == "MODEL_AUTH_FAILED"
    assert {c.build_id for lane in _results(env) for c in lane} == {old.build_id}
    with Session(env[1]) as session:
        doc = session.get(Document, UUID(uploaded["document_id"]))
        (env[2] / doc.storage_key).write_text("changed limit 999 CNY")
    env[0].post(path + "/rebuild", headers=env[6])
    assert _work(env)["error_code"] == "DOCUMENT_HASH_MISMATCH"
    assert {c.build_id for lane in _results(env) for c in lane} == {old.build_id}
    assert env[0].get(_source(env, old), headers=env[8]).status_code == 200


def test_rebuild_rejects_model_and_dimension_change_before_queue_or_call(
    job_env, monkeypatch
):
    env = job_env
    uploaded = upload(env)
    _work(env)
    old = _results(env)[0][0]
    path = _url(env, uploaded)
    original = jobs.configured_profile
    monkeypatch.setattr(
        jobs,
        "configured_profile",
        lambda settings: EmbeddingProfile("fake", "another-model"),
    )
    result = env[0].post(path + "/rebuild", headers=env[6])
    assert (
        result.status_code == 409
        and result.json()["error"]["code"] == "INCOMPATIBLE_REBUILD_CONFIG"
    )
    incompatible = FakeEmbeddingClient()
    incompatible.model = "another-model"
    with pytest.raises(IngestError, match="INCOMPATIBLE_REBUILD_CONFIG"):
        ingest_document(
            sessionmaker(env[1]),
            UUID(uploaded["document_id"]),
            env[2],
            incompatible,
            EmbeddingProfile("fake", "another-model"),
        )
    assert incompatible.call_count == 0
    monkeypatch.setattr(jobs, "configured_profile", original)
    settings = env[0].app.state.settings
    monkeypatch.setattr(
        env[0].app.state,
        "settings",
        settings.model_copy(update={"embedding_dimensions": 1024}),
    )
    result = env[0].post(path + "/rebuild", headers=env[6])
    assert (
        result.status_code == 503
        and result.json()["error"]["code"] == "INVALID_EMBEDDING_CONFIG"
    )
    with Session(env[1]) as session:
        assert session.scalar(select(func.count()).select_from(IngestionJob)) == 1
        assert (
            session.get(Document, UUID(uploaded["document_id"])).active_build_id
            == old.build_id
        )


def test_same_filename_different_bytes_does_not_overwrite(job_env):
    env = job_env
    first = upload(env)
    second = _upload(
        env[0], env[4], env[6], "demo.txt", b"another limit 700 CNY"
    ).json()
    assert second["document_id"] != first["document_id"]
    assert (
        env[0].get(_url(env, first) + "/raw", headers=env[6]).content
        == b"Demo: limit 680 CNY."
    )


def test_cleanup_refuses_traversal_symlinks_and_unsupported_platform(
    tmp_path, monkeypatch
):
    storage = tmp_path / "uploads"
    objects = storage / "objects"
    objects.mkdir(parents=True)
    name = "a" * 32
    outside = tmp_path / name
    outside.write_text("must survive")
    assert documents.cleanup_original(storage, "../" + name) == "blocked"
    if not CAN_CLEANUP:
        assert documents.cleanup_original(storage, "objects/" + name) == "pending"
        return
    (objects / name).symlink_to(outside)
    assert documents.cleanup_original(storage, "objects/" + name) == "blocked"
    (objects / name).unlink()
    objects.rmdir()
    objects.symlink_to(tmp_path, target_is_directory=True)
    assert documents.cleanup_original(storage, "objects/" + name) == "blocked"
    assert outside.read_text() == "must survive"
    monkeypatch.setattr(os, "supports_dir_fd", set())
    assert documents.cleanup_original(storage, "objects/" + name) == "pending"


def test_file_cleanup_failure_is_visible_and_retryable(job_env, monkeypatch):
    env = job_env
    uploaded = upload(env)
    path = _url(env, uploaded)
    original = documents.cleanup_original
    monkeypatch.setattr(documents, "cleanup_original", lambda *args: "pending")
    response = env[0].delete(path, headers=env[6])
    assert (
        response.status_code == 200 and response.json()["cleanup_status"] == "pending"
    )
    assert env[0].get(path + "/raw", headers=env[6]).status_code == 404
    monkeypatch.setattr(documents, "cleanup_original", original)
    assert env[0].delete(path, headers=env[6]).json()["cleanup_status"] == REMOVED


@pytest.mark.parametrize("first", ["delete", "enqueue"])
def test_delete_and_enqueue_serialize_without_leaving_active_jobs(
    job_env, monkeypatch, first
):
    env = job_env
    uploaded = upload(env)
    _work(env)
    path = _url(env, uploaded)
    held, release, other_started = Event(), Event(), Event()
    target = documents if first == "delete" else jobs
    original = target.lock_document_operation

    def hold_after_lock(session, document_id):
        original(session, document_id)
        held.set()
        assert release.wait(10)

    monkeypatch.setattr(target, "lock_document_operation", hold_after_lock)

    def request(action, second=False):
        if second:
            other_started.set()
        if action == "delete":
            return env[0].delete(path, headers=env[6])
        return env[0].post(path + "/rebuild", headers=env[6])

    with ThreadPoolExecutor(max_workers=2) as pool:
        leader = pool.submit(request, first)
        try:
            assert held.wait(10)
            follower = pool.submit(
                request, "enqueue" if first == "delete" else "delete", True
            )
            assert other_started.wait(10)
        finally:
            release.set()
        first_result = leader.result(timeout=10)
        second_result = follower.result(timeout=10)
    assert first_result.status_code == (200 if first == "delete" else 202)
    assert second_result.status_code == (404 if first == "delete" else 200)
    with Session(env[1]) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(IngestionJob)
                .where(IngestionJob.status.in_(jobs.ACTIVE))
            )
            == 0
        )
        assert session.get(Document, UUID(uploaded["document_id"])).deleted_at
    assert _work(env) is None
