import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import Markdown from "react-markdown";
import { api, ApiError, cancelled } from "./api";
import type {
  AgentSummary,
  AnswerMode,
  AnswerResult,
  Citation,
  KnowledgeBase,
  SourceChunk,
} from "./api";

const answerLabels = {
  answered: "已回答",
  insufficient_evidence: "资料不足",
  needs_clarification: "需要补充信息",
};
const eventLabels: Record<string, string> = {
  running: "未完成",
  success: "成功",
  no_results: "无结果",
  permission_denied: "权限拒绝",
  invalid_arguments: "参数无效",
  technical_failure: "技术失败",
  budget_exceeded: "达到预算",
};
const endLabels: Record<string, string> = {
  model_finished: "本轮已完成",
  clarification: "等待补充条件",
  tool_budget: "已达到工具调用上限",
  model_budget: "已达到模型请求上限",
  context_budget: "已达到上下文上限",
  repeated_call: "已阻止重复工具调用",
  deadline: "本轮超时，已结束",
  error: "本轮执行失败",
};
function message(error: unknown) {
  if (!(error instanceof ApiError)) return "请求失败，请重试。";
  if (error.status === 404 || error.status === 403)
    return "知识库或来源不可访问，可能已删除或权限已失效。请返回知识库重新选择。";
  if (error.status === 410)
    return "此引用的旧构建已失效，请重新提问获取当前来源。";
  if (error.status === 409) return "回答期间资料已变化，请重新提问。";
  if (error.status === 504 || error.code === "TIMEOUT")
    return "问答请求超时，未获得已校验回答。可以稍后重试。";
  if (error.code === "MODEL_NOT_CONFIGURED")
    return "后端尚未配置问答模型，请联系管理员。";
  if (
    [
      "ANSWER_INVALID_CITATIONS",
      "MODEL_INVALID_RESPONSE",
      "MODEL_INVALID_TOOL_CALL",
    ].includes(error.code)
  )
    return "模型输出未通过后端校验，未展示为成功回答。请重试。";
  return error.message;
}
function ErrorNotice({ error }: { error: unknown }) {
  return error ? (
    <div className="error" role="alert">
      {message(error)}
      {error instanceof ApiError && error.requestId && (
        <small>请求编号：{error.requestId}</small>
      )}
    </div>
  ) : null;
}
function location(source: SourceChunk) {
  return (
    [
      source.page_number !== null ? `第 ${source.page_number} 页` : null,
      source.heading_path.length ? source.heading_path.join(" / ") : null,
      source.start_line !== null
        ? `第 ${source.start_line}–${source.end_line ?? source.start_line} 行`
        : null,
    ]
      .filter(Boolean)
      .join(" · ") || "来源片段"
  );
}
function Events({ summary }: { summary: AgentSummary }) {
  if (summary.mode !== "agent") return null;
  return (
    <section className="agent-events" aria-label="工具执行事件">
      <h3>本轮工具记录</h3>
      <p className="muted">
        请求结束后统一展示 · 工具 {summary.tool_call_count ?? "未知"} 次 ·
        模型请求 {summary.model_call_count ?? "未知"} 次
      </p>
      {summary.events.length ? (
        <ol>
          {summary.events.map((event) => (
            <li key={event.step}>
              <strong>
                {event.tool === "search_knowledge" ? "搜索知识库" : "补读片段"}
              </strong>
              <span>
                {eventLabels[event.status] ?? "未知状态"}
                {event.result_count !== null
                  ? ` · ${event.result_count} 条结果`
                  : ""}
              </span>
              {event.query_summary && <p>{event.query_summary}</p>}
            </li>
          ))}
        </ol>
      ) : (
        <p className="muted">本轮没有可展示的工具执行事件。</p>
      )}
      {summary.termination_reason && (
        <p className="notice">
          {endLabels[summary.termination_reason] ?? "本轮已结束"}
        </p>
      )}
    </section>
  );
}

export default function Question({
  kb,
  bases,
  onSelect,
  onBack,
}: {
  kb: KnowledgeBase;
  bases: KnowledgeBase[];
  onSelect: (id: string) => void;
  onBack: () => void;
}) {
  const [question, setQuestion] = useState("");
  const [mode, setMode] = useState<AnswerMode>("rag");
  const [result, setResult] = useState<AnswerResult | null>(null);
  const [submitted, setSubmitted] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [citation, setCitation] = useState<Citation | null>(null);
  const [source, setSource] = useState<SourceChunk | null>(null);
  const [sourceError, setSourceError] = useState<unknown>(null);
  const run = useRef(0);
  const answerController = useRef<AbortController | null>(null);
  const sourceController = useRef<AbortController | null>(null);
  const sourceRun = useRef(0);
  const inFlight = useRef(false);
  function clearSource() {
    sourceRun.current++;
    sourceController.current?.abort();
    setCitation(null);
    setSource(null);
    setSourceError(null);
  }
  function clearRound() {
    run.current++;
    answerController.current?.abort();
    inFlight.current = false;
    setPending(false);
    setResult(null);
    setError(null);
    setSubmitted("");
    clearSource();
  }
  useEffect(() => {
    const leave = () => {
      run.current++;
      sourceRun.current++;
      answerController.current?.abort();
      sourceController.current?.abort();
    };
    window.addEventListener("pagehide", leave);
    return () => {
      leave();
      window.removeEventListener("pagehide", leave);
    };
  }, []);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (inFlight.current) return;
    clearRound();
    const value = question.trim();
    if (!value || question.length > 4000) {
      setError(new ApiError("请输入 1～4000 字的问题。"));
      return;
    }
    const revision = run.current;
    const controller = new AbortController();
    answerController.current = controller;
    inFlight.current = true;
    setPending(true);
    setSubmitted(value);
    try {
      const answer = await api.answer(kb.id, value, mode, controller.signal);
      if (revision === run.current && !controller.signal.aborted)
        setResult(answer);
    } catch (reason) {
      if (revision === run.current && !cancelled(reason)) setError(reason);
    } finally {
      if (revision === run.current) {
        inFlight.current = false;
        setPending(false);
      }
    }
  }
  async function read(c: Citation) {
    clearSource();
    const revision = ++sourceRun.current;
    const controller = new AbortController();
    sourceController.current = controller;
    setCitation(c);
    try {
      const value = await api.source(kb.id, c, controller.signal);
      if (revision === sourceRun.current && !controller.signal.aborted)
        setSource(value);
    } catch (reason) {
      if (revision === sourceRun.current && !cancelled(reason)) {
        setSourceError(reason);
        setSource(null);
      }
    }
  }
  const summary =
    result ?? (error instanceof ApiError ? error.agent : undefined);
  return (
    <section className="question-page" aria-label="单轮问答">
      <div className="document-nav">
        <button className="text-button" onClick={onBack}>
          ← 返回知识库
        </button>
        <label>
          当前知识库
          <select
            aria-label="问答知识库"
            value={kb.id}
            onChange={(event) => onSelect(event.target.value)}
          >
            {bases.map((base) => (
              <option value={base.id} key={base.id}>
                {base.name}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="page-title">
        <div>
          <p className="eyebrow">ASK YOUR LIBRARY</p>
          <h1>向知识库提问</h1>
          <p className="muted">{kb.name} · 从授权资料中寻找依据</p>
        </div>
        <span className="demo-badge">单轮问答</span>
      </div>
      <form className="question-form" onSubmit={(event) => void submit(event)}>
        <fieldset className="mode-options">
          <legend>回答模式</legend>
          {(["rag", "agent"] as AnswerMode[]).map((value) => (
            <label key={value}>
              <input
                type="radio"
                name="mode"
                value={value}
                checked={mode === value}
                onChange={() => {
                  clearRound();
                  setMode(value);
                }}
              />
              <span>
                <strong>{value === "rag" ? "固定 RAG" : "Agent"}</strong>
                <small>
                  {value === "rag"
                    ? "检索一次，再依据来源回答"
                    : "按工具结果决定是否继续检索"}
                </small>
              </span>
            </label>
          ))}
        </fieldset>
        <label htmlFor="question-input">你的问题</label>
        <textarea
          id="question-input"
          value={question}
          maxLength={4000}
          rows={4}
          placeholder="例如：报销需要在几天内提交？有哪些例外？"
          onChange={(event) => setQuestion(event.target.value)}
          aria-describedby="question-help"
          disabled={pending}
        />
        <div className="question-submit">
          <p id="question-help">
            每次提问独立处理，不携带上一次问答。
            <span>{question.length} / 4000</span>
          </p>
          <button className="primary" disabled={pending}>
            {pending ? "正在查阅资料…" : "提交问题"}
          </button>
        </div>
      </form>
      {pending && (
        <div className="loading-panel" role="status">
          <span className="spinner" />
          正在生成并校验回答，请稍候。
          {mode === "agent" && <span>Agent 最多运行 60 秒。</span>}
        </div>
      )}
      <ErrorNotice error={error} />
      {result && (
        <article className="answer-panel" aria-label="问答结果">
          <header>
            <span
              className={`status-badge ${result.status === "answered" ? "ready" : "queued"}`}
            >
              {answerLabels[result.status]}
            </span>
            <small>请求 {result.request_id}</small>
          </header>
          <p className="submitted-question">{submitted}</p>
          <div className="answer-markdown">
            <Markdown
              skipHtml
              components={{
                a: ({ children }) => <span>{children}</span>,
                img: ({ alt }) => (
                  <span>{alt ? `[图片：${alt}]` : "[图片]"}</span>
                ),
              }}
            >
              {result.answer}
            </Markdown>
          </div>
          {result.citations.length > 0 && (
            <section className="answer-citations" aria-label="回答引用">
              <h3>资料来源</h3>
              <p className="muted">
                点击重新验证并查看原文。引用有效不等于结论一定正确。
              </p>
              {result.citations.map((c) => (
                <button
                  className="citation-button"
                  key={c.citation_id}
                  onClick={() => void read(c)}
                >
                  <span>[{c.citation_id}]</span>
                  <span>
                    <strong>{c.document_name}</strong>
                    <small>{location(c)}</small>
                  </span>
                  <span aria-hidden="true">↗</span>
                </button>
              ))}
            </section>
          )}
        </article>
      )}
      {summary && <Events summary={summary} />}
      {citation && (
        <section className="source-panel" aria-label="引用原文">
          <div className="preview-heading">
            <h2>引用 [{citation.citation_id}]</h2>
            <button className="text-button" onClick={clearSource}>
              关闭来源
            </button>
          </div>
          <ErrorNotice error={sourceError} />
          {!source && !sourceError && <p role="status">正在验证来源权限…</p>}
          {source && (
            <>
              <h3>{source.document_name}</h3>
              <p className="muted">{location(source)}</p>
              <pre>{source.snippet}</pre>
            </>
          )}
        </section>
      )}
      {!pending && !result && !error && (
        <div className="question-empty">
          <span aria-hidden="true">“</span>
          <h2>带着问题来，带着依据走。</h2>
          <p>先选择回答模式，再输入一个具体问题。资料不足时会明确说明。</p>
        </div>
      )}
    </section>
  );
}
