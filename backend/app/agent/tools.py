"""Two request-scoped read-only tools. No agent loop or dynamic execution."""

from contextlib import nullcontext
from threading import Lock
from uuid import UUID

from pydantic import ValidationError

from app.agent.contracts import (
    ReadArguments,
    RunContext,
    SearchArguments,
    ToolError,
    ToolItem,
    ToolLimits,
    ToolResult,
)
from app.llm.contracts import ModelError
from app.services import retrieval
from app.services.knowledge_bases import NotFound, require_kb_member
from app.services.tool_sources import ChunkPermit, EvidenceExpired, read_permitted
from app.services.traces import error_code


class KnowledgeTools:
    def __init__(
        self,
        context: RunContext,
        factory,
        profile,
        embedding_factory,
        *,
        limits: ToolLimits | None = None,
        trace=None,
    ):
        if not isinstance(context, RunContext):
            raise TypeError("A backend RunContext is required")
        if trace is not None and (
            trace.user_id != context.user_id
            or trace.kb_id != context.kb_id
            or trace.request_id != context.request_id
        ):
            raise ValueError("Trace identity must match the tool context")
        self._context, self._factory, self._profile = context, factory, profile
        self._embedding_factory = embedding_factory
        self._limits = limits or ToolLimits()
        self._remaining = self._limits.total_text_chars
        self._allowed: dict[UUID, ChunkPermit] = {}
        self._trace = trace
        self._lock = Lock()  # Serialize mutable per-run permits and output budget.

    @property
    def context(self):
        return self._context

    def search_knowledge(self, query, top_k=5) -> dict:
        return self.call("search_knowledge", {"query": query, "top_k": top_k})

    def read_chunks(self, chunk_ids) -> dict:
        return self.call("read_chunks", {"chunk_ids": chunk_ids})

    def _stage(self, name, kind="stage"):
        return (
            self._trace.stage(name, kind=kind)
            if self._trace is not None
            else nullcontext()
        )

    def _result(self, name, status, code=None, message=None, *, retryable=False):
        return ToolResult(
            tool=name,
            request_id=self.context.request_id,
            status=status,
            error=ToolError(code=code, message=message, retryable=retryable)
            if code
            else None,
            remaining_text_chars=self._remaining,
        )

    def call(self, name: str, arguments: dict) -> dict:
        # Explicit registry: never resolve tool names using eval/getattr/imports.
        tool = name if name in ("search_knowledge", "read_chunks") else None
        with self._lock:
            if tool == "search_knowledge":
                self._allowed.clear()
            with self._stage(f"tool.{tool or 'unknown'}", "tool") as event:
                try:
                    with self._factory() as session:
                        require_kb_member(
                            session, self.context.user_id, self.context.kb_id
                        )
                    result = self._dispatch(tool, arguments)
                except NotFound:
                    self._allowed.clear()
                    result = self._result(
                        tool,
                        "permission_denied",
                        "ACCESS_DENIED",
                        "Knowledge base is not accessible",
                    )
                except EvidenceExpired:
                    self._allowed.clear()
                    result = self._result(
                        tool,
                        "no_results",
                        "EVIDENCE_EXPIRED",
                        "Retrieved evidence changed; search again",
                    )
                except (retrieval.RetrievalError, ModelError) as exc:
                    code = error_code(exc)
                    result = self._result(
                        tool,
                        "technical_failure",
                        code,
                        "Retrieval or model operation failed",
                        retryable=code
                        in {
                            "MODEL_TIMEOUT",
                            "MODEL_NETWORK_ERROR",
                            "MODEL_UNAVAILABLE",
                        },
                    )
                except Exception:
                    result = self._result(
                        tool,
                        "technical_failure",
                        "TOOL_EXECUTION_FAILED",
                        "Tool execution failed",
                    )
                if event is not None:
                    event["status"] = (
                        "complete"
                        if result.status in {"success", "no_results"}
                        else "error"
                    )
                    event["error_type"] = result.error.code if result.error else None
                return result.model_dump(mode="json")

    def _dispatch(self, name, arguments):
        if name is None:
            return self._result(
                None, "invalid_arguments", "UNKNOWN_TOOL", "Unknown tool"
            )
        try:
            if not isinstance(arguments, dict):
                return self._result(
                    name,
                    "invalid_arguments",
                    "INVALID_ARGUMENTS",
                    "Expected an argument object",
                )
            params = (
                SearchArguments if name == "search_knowledge" else ReadArguments
            ).model_validate(arguments)
        except ValidationError:
            # Never echo ValidationError: it may contain source text or a secret input.
            return self._result(
                name, "invalid_arguments", "INVALID_ARGUMENTS", "Invalid tool arguments"
            )
        if self._remaining == 0:
            return self._result(
                name,
                "budget_exceeded",
                "OUTPUT_BUDGET_EXHAUSTED",
                "Run text budget exhausted",
            )
        if name == "search_knowledge":
            return self._search(params)
        ids = [UUID(value) for value in params.chunk_ids]
        if any(chunk_id not in self._allowed for chunk_id in ids):
            return self._result(
                name,
                "permission_denied",
                "CHUNK_NOT_AVAILABLE",
                "Chunks are not available in the current search",
            )
        permits = [self._allowed[chunk_id] for chunk_id in ids]
        with self._stage("tool_source_validation"):
            sources = read_permitted(
                self._factory, self.context.user_id, self.context.kb_id, permits
            )
        return self._emit(name, sources, permits, self._limits.read_text_chars)

    def _search(self, params):
        embedding_factory = self._embedding_factory
        if self._trace is not None:
            embedding_factory = self._trace.wrap_factory("embedding", embedding_factory)
        with self._stage("retrieval"):
            chunks = retrieval.search(
                self._factory,
                self.context.user_id,
                self.context.kb_id,
                params.query,
                params.top_k,
                self._profile,
                embedding_factory,
            )
        if not chunks:
            return self._result("search_knowledge", "no_results")
        if (
            len(chunks) > params.top_k
            or len({c.chunk_id for c in chunks}) != len(chunks)
            or any(c.knowledge_base_id != self.context.kb_id for c in chunks)
        ):
            return self._result(
                "search_knowledge",
                "technical_failure",
                "INVALID_SEARCH_RESULT",
                "Search returned invalid candidates",
            )
        permits = [ChunkPermit.from_retrieved(chunk) for chunk in chunks]
        with self._stage("tool_source_validation"):
            sources = read_permitted(
                self._factory, self.context.user_id, self.context.kb_id, permits
            )
        result = self._emit(
            "search_knowledge", sources, permits, self._limits.search_text_chars
        )
        returned = {item.chunk_id for item in result.items}
        self._allowed = {
            permit.chunk_id: permit for permit in permits if permit.chunk_id in returned
        }
        if self._trace is not None:
            self._trace.candidates_from(
                [chunk for chunk in chunks if chunk.chunk_id in returned]
            )
        return result

    def _emit(self, name, sources, permits, text_limit):
        items = []
        used = 0

        def compose(candidates):
            omitted = len(sources) - len(candidates)
            return ToolResult(
                tool=name,
                request_id=self.context.request_id,
                status="success",
                items=candidates,
                truncated=bool(
                    omitted or any(item.truncated_fields for item in candidates)
                ),
                omitted_count=omitted,
                remaining_text_chars=self._remaining
                - sum(len(item.text) for item in candidates),
            )

        for source, permit in zip(sources, permits, strict=True):
            limit = min(len(source.snippet), text_limit, self._remaining - used)
            if limit <= 0:
                break
            heading = []
            heading_budget = 256
            for part in (source.heading_path or [])[:8]:
                piece = part[: min(64, heading_budget)]
                if not piece:
                    break
                heading.append(piece)
                heading_budget -= len(piece)
            base_fields = []
            if heading != (source.heading_path or []):
                base_fields.append("heading_path")
            if len(source.document_name) > 160:
                base_fields.append("document_name")

            def candidate(length):
                return ToolItem(
                    chunk_id=source.chunk_id,
                    document_id=source.document_id,
                    build_id=source.build_id,
                    document_name=source.document_name[:160],
                    text=source.snippet[:length],
                    page_number=source.page_number,
                    heading_path=heading,
                    start_line=source.start_line,
                    end_line=source.end_line,
                    rank=permit.rank,
                    distance=permit.distance,
                    truncated_fields=base_fields
                    + (["text"] if length < len(source.snippet) else []),
                )

            # JSON escaping and metadata also count toward each response's hard limit.
            full = candidate(limit)
            if (
                len(compose(items + [full]).model_dump_json())
                <= self._limits.response_chars
            ):
                items.append(full)
                used += len(full.text)
                continue
            low, high, best = 1, limit - 1, None
            while low <= high:
                length = (low + high) // 2
                item = candidate(length)
                if (
                    len(compose(items + [item]).model_dump_json())
                    <= self._limits.response_chars
                ):
                    best, low = item, length + 1
                else:
                    high = length - 1
            if best is None:
                break
            items.append(best)
            used += len(best.text)
        if not items:
            return self._result(
                name,
                "budget_exceeded",
                "OUTPUT_BUDGET_EXHAUSTED",
                "Output budget cannot fit a source",
            )
        result = compose(items)
        self._remaining = result.remaining_text_chars
        return result
