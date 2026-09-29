"""Bounded current-build previews; real DB, explicitly fake embeddings."""

from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker
from test_documents import document_env as document_env
from test_ingestion_jobs import job_env as job_env
from test_ingestion_jobs import upload

from app.llm.fake import FakeEmbeddingClient
from app.models import Chunk, Document, IngestionJob
from app.services import ingestion_jobs as jobs


def work(env):
    return jobs.process_one(
        sessionmaker(env[1]),
        env[2],
        lambda _: FakeEmbeddingClient(),
        heartbeat_interval=None,
    )


def test_preview_authorization_lifecycle_and_job_visibility(job_env):
    client, engine, _, users, a, b, alice, bob, carol = job_env
    accepted = upload(job_env, "演示数据：额度 680 元。".encode())
    path = f"/knowledge-bases/{a}/documents/{accepted['document_id']}"
    assert client.get(path + "/preview", headers=alice).json()["items"] == []
    assert (
        client.get(path, headers=alice).json()["latest_job"]["job_id"]
        == accepted["job_id"]
    )
    assert client.get(path, headers=carol).json()["latest_job"] is None
    assert (
        client.get(f"/knowledge-bases/{a}/documents", headers=carol).json()["items"][0][
            "latest_job"
        ]
        is None
    )
    assert client.get(accepted["status_url"], headers=carol).status_code == 403
    work(job_env)
    data = client.get(path + "/preview", headers=carol).json()
    assert data["total_chunks"] == 1
    assert data["items"][0]["text"] == "演示数据：额度 680 元。"
    assert data["items"][0]["start_line"] == 1
    assert data["items"][0]["page_number"] is None
    assert client.get(path + "/preview", headers=bob).status_code == 404
    assert client.get(path.replace(a, b) + "/preview", headers=bob).status_code == 404
    assert client.get(path + "/preview").status_code == 401
    next_job = client.post(path + "/rebuild", headers=alice).json()
    assert (
        client.get(path + "/preview", headers=alice).json()["build_id"]
        == data["build_id"]
    )
    with Session(engine) as session:
        job = session.get(IngestionJob, UUID(next_job["job_id"]))
        job.status = "failed"
        job.error_code = "MODEL_TIMEOUT"
        job.error_summary = "MODEL_TIMEOUT"
        session.commit()
    detail = client.get(path, headers=alice).json()
    assert detail["status"] == "ready"
    assert detail["latest_job"]["error_code"] == "MODEL_TIMEOUT"
    assert (
        client.get(path + "/preview", headers=carol).json()["build_id"]
        == data["build_id"]
    )
    client.post(path + "/rebuild", headers=alice)
    work(job_env)
    new = client.get(path + "/preview", headers=alice).json()
    assert new["build_id"] != data["build_id"]
    assert not {i["chunk_id"] for i in data["items"]} & {
        i["chunk_id"] for i in new["items"]
    }
    assert (
        client.delete(
            f"/knowledge-bases/{a}/members/{users['carol']}", headers=alice
        ).status_code
        == 200
    )
    assert client.get(path + "/preview", headers=carol).status_code == 404
    assert client.delete(path, headers=alice).status_code == 200
    assert client.get(path + "/preview", headers=alice).status_code == 404


def test_preview_limits_and_current_ready_build_only(job_env):
    client, engine, _, _, a, _, alice, _, _ = job_env
    accepted = upload(job_env)
    work(job_env)
    path = f"/knowledge-bases/{a}/documents/{accepted['document_id']}"
    with Session(engine) as session:
        doc = session.get(Document, UUID(accepted["document_id"]))
        for ordinal in range(1, 5):
            session.add(
                Chunk(
                    build_id=doc.active_build_id,
                    ordinal=ordinal,
                    body="中" * 900,
                    content_sha256="a" * 64,
                    page_number=ordinal + 1,
                    heading_path=["长" * 200] * 7,
                )
            )
        session.commit()
    data = client.get(path + "/preview", headers=alice).json()
    assert data["total_chunks"] == 5
    assert len(data["items"]) == 3
    assert [i["ordinal"] for i in data["items"]] == [0, 1, 2]
    item = data["items"][1]
    assert len(item["text"]) == 600 and item["truncated"]
    assert item["page_number"] == 2
    assert len(item["heading_path"]) == 6 and item["locator_truncated"]
