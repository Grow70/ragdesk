"""Local operator command for one uploaded document build."""

import argparse
import json
import sys
from uuid import UUID

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import load_settings
from app.llm.fake import FakeEmbeddingClient
from app.llm.openai import OpenAIEmbeddingClient
from app.services.ingest import EmbeddingProfile, IngestError, ingest_document


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Index one existing private document")
    parser.add_argument("--document-id", type=UUID, required=True)
    parser.add_argument("--build-id", type=UUID)
    parser.add_argument(
        "--embedding-backend", choices=("fake", "openai"), required=True
    )
    parser.add_argument("--chunk-size", type=int, default=600)
    parser.add_argument("--overlap", type=int, default=80)
    parser.add_argument("--batch-size", type=int, default=16)
    args = parser.parse_args(argv)

    engine = None
    embedding = None
    try:
        settings = load_settings()
        if args.embedding_backend == "fake":
            embedding = FakeEmbeddingClient(dimensions=settings.embedding_dimensions)
        else:
            if settings.openai_api_key is None:
                raise IngestError("OPENAI_API_KEY_REQUIRED")
            embedding = OpenAIEmbeddingClient(
                api_key=settings.openai_api_key.get_secret_value(),
                model=settings.embedding_model,
                dimensions=settings.embedding_dimensions,
                connect_timeout=settings.model_connect_timeout_seconds,
                read_timeout=settings.model_read_timeout_seconds,
                max_attempts=settings.model_max_attempts,
            )
        profile = EmbeddingProfile(
            provider=embedding.provider,
            model=embedding.model,
            dimensions=embedding.dimensions,
            chunk_size=args.chunk_size,
            overlap=args.overlap,
            batch_size=args.batch_size,
        )
        engine = create_engine(settings.database_url.get_secret_value())
        outcome = ingest_document(
            sessionmaker(engine),
            args.document_id,
            settings.upload_storage_dir,
            embedding,
            profile,
            args.build_id,
        )
        print(
            json.dumps(
                {
                    "build_id": str(outcome.build_id),
                    "status": outcome.status,
                    "chunk_count": outcome.chunk_count,
                    "model_config_id": outcome.model_config_id,
                    "reused": outcome.reused,
                    "error_code": outcome.error_code,
                },
                ensure_ascii=False,
            )
        )
        return 0 if outcome.status == "ready" else 1
    except IngestError as exc:
        print(json.dumps({"error_code": exc.code}), file=sys.stderr)
        return 2
    except (RuntimeError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2
    finally:
        if isinstance(embedding, OpenAIEmbeddingClient):
            embedding.close()
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
