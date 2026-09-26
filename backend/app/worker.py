"""Run exactly one trusted local ingestion worker; recovery is not implemented."""

import argparse
import json
import math
import sys
import time

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import load_settings
from app.http import configure_logging
from app.llm.fake import FakeEmbeddingClient
from app.llm.openai import OpenAIEmbeddingClient
from app.services.ingest import IngestError
from app.services.ingestion_jobs import process_one


def embedding_client(settings, profile):
    if profile.provider == "fake":
        return FakeEmbeddingClient(dimensions=profile.dimensions)
    if settings.openai_api_key is None:
        raise IngestError("OPENAI_API_KEY_REQUIRED")
    return OpenAIEmbeddingClient(
        api_key=settings.openai_api_key.get_secret_value(),
        model=profile.model,
        dimensions=profile.dimensions,
        connect_timeout=settings.model_connect_timeout_seconds,
        read_timeout=settings.model_read_timeout_seconds,
        max_attempts=settings.model_max_attempts,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--once", action="store_true", help="Process at most one job and exit"
    )
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    args = parser.parse_args(argv)
    if not math.isfinite(args.poll_seconds) or not 0 < args.poll_seconds <= 60:
        parser.error("--poll-seconds must be finite and between 0 and 60")
    engine = None
    try:
        settings = load_settings()
        configure_logging()
        engine = create_engine(
            settings.database_url.get_secret_value(), pool_pre_ping=True
        )
        factory = sessionmaker(engine)
        while True:
            result = process_one(
                factory,
                settings.upload_storage_dir,
                lambda profile: embedding_client(settings, profile),
            )
            if result is not None:
                print(json.dumps(result), flush=True)
            if args.once:
                if result is None:
                    print(json.dumps({"status": "idle"}), flush=True)
                return 1 if result and result["status"] == "failed" else 0
            if result is None:
                time.sleep(args.poll_seconds)
    except KeyboardInterrupt:
        return 130
    except Exception:
        # No credentials/DB URLs/provider response bodies in CLI diagnostics.
        print(json.dumps({"error_code": "WORKER_STOPPED"}), file=sys.stderr)
        return 2
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
