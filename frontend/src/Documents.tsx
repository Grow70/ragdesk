import { useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { api, ApiError, cancelled } from "./api";
import type {
  ChunkPreview,
  DocumentItem,
  IngestionJob,
  KnowledgeBase,
} from "./api";

const labels = {
  uploaded: "已上传",
  queued: "排队中",
  processing: "处理中",
  ready: "可检索",
  failed: "失败",
  running: "处理中",
  succeeded: "已完成",
};
const active = (job: IngestionJob) =>
  job.status === "queued" || job.status === "running";
function ErrorNotice({ error }: { error: unknown }) {
  if (!error) return null;
  return (
    <div className="error" role="alert">
      {error instanceof ApiError ? error.message : "操作失败，请重试。"}
      {error instanceof ApiError && error.requestId && (
        <small>请求编号：{error.requestId}</small>
      )}
    </div>
  );
}

function JobStatus({
  kb,
  doc,
  initial,
  onTerminal,
}: {
  kb: string;
  doc: string;
  initial: IngestionJob;
  onTerminal: (id: string) => void;
}) {
  const [job, setJob] = useState(initial);
  const [stopped, setStopped] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [retry, setRetry] = useState(0);
  const terminal = useRef(onTerminal);
  terminal.current = onTerminal;
  useEffect(() => {
    if (!active(initial)) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    let calls = 0;
    let done = false;
    setStopped("");
    setError(null);
    const stop = (message: string) => {
      done = true;
      clearTimeout(timer);
      controller.abort();
      setStopped(message);
    };
    const deadline = setTimeout(
      () => stop("自动检查已达 2 分钟，后台任务仍可能继续。"),
      120_000,
    );
    const leave = () => stop("已停止自动检查。");
    window.addEventListener("pagehide", leave);
    async function poll() {
      if (done) return;
      calls++;
      try {
        const next = await api.job(kb, doc, initial.job_id, controller.signal);
        if (done) return;
        setJob(next);
        if (!active(next)) {
          done = true;
          clearTimeout(deadline);
          terminal.current(doc);
          return;
        }
        if (calls >= 60) {
          stop("自动检查已达 60 次，后台任务仍可能继续。");
          clearTimeout(deadline);
          return;
        }
        timer = setTimeout(() => void poll(), 2_000);
      } catch (reason) {
        if (done || cancelled(reason)) return;
        setError(reason);
        stop("自动检查已停止。");
        clearTimeout(deadline);
      }
    }
    timer = setTimeout(() => void poll(), 2_000);
    return () => {
      done = true;
      clearTimeout(timer);
      clearTimeout(deadline);
      controller.abort();
      window.removeEventListener("pagehide", leave);
    };
    // The job identity defines one bounded run; parent metadata refreshes do not restart it.
  }, [kb, doc, initial.job_id, retry]);
  return (
    <div className="job-status">
      <span>最近任务：{labels[job.status]}</span>
      <small>
        尝试 {job.attempts} / {job.max_attempts}
      </small>
      {job.status === "failed" && (
        <p className="failure-reason">
          {job.error_code || "UNKNOWN_ERROR"}
          {job.error_summary && job.error_summary !== job.error_code
            ? ` · ${job.error_summary}`
            : ""}
        </p>
      )}
      <ErrorNotice error={error} />
      {stopped && (
        <>
          <small role="status">{stopped}</small>
          <button
            className="text-button"
            onClick={() => setRetry((n) => n + 1)}
          >
            重新检查任务
          </button>
        </>
      )}
    </div>
  );
}

export default function Documents({
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
  const admin = kb.role === "admin";
  const [items, setItems] = useState<DocumentItem[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [reload, setReload] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [previewDoc, setPreviewDoc] = useState<DocumentItem | null>(null);
  const [preview, setPreview] = useState<ChunkPreview | null>(null);
  const [previewError, setPreviewError] = useState<unknown>(null);
  const [deleting, setDeleting] = useState<DocumentItem | null>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const lifetime = useRef(new AbortController());
  const actionBusy = useRef(false);
  useEffect(() => {
    lifetime.current = new AbortController();
    const controller = lifetime.current;
    const leave = () => controller.abort();
    window.addEventListener("pagehide", leave);
    return () => {
      controller.abort();
      window.removeEventListener("pagehide", leave);
    };
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    const leave = () => controller.abort();
    window.addEventListener("pagehide", leave);
    setLoading(true);
    setError(null);
    setItems([]);
    setPreviewDoc(null);
    void api
      .documents(kb.id, offset, controller.signal)
      .then((data) => {
        if (controller.signal.aborted) return;
        if (offset && offset >= data.total) {
          setOffset(Math.max(0, Math.ceil(data.total / 10) * 10 - 10));
          return;
        }
        setItems(data.items);
        setTotal(data.total);
      })
      .catch((reason) => {
        if (!cancelled(reason)) setError(reason);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => {
      controller.abort();
      window.removeEventListener("pagehide", leave);
    };
  }, [kb.id, offset, reload]);
  useEffect(() => {
    setPreview(null);
    setPreviewError(null);
    if (!previewDoc) return;
    const controller = new AbortController();
    const leave = () => controller.abort();
    window.addEventListener("pagehide", leave);
    void api
      .preview(kb.id, previewDoc.document_id, controller.signal)
      .then(setPreview)
      .catch((reason) => {
        if (!cancelled(reason)) setPreviewError(reason);
      });
    return () => {
      controller.abort();
      window.removeEventListener("pagehide", leave);
    };
  }, [kb.id, previewDoc]);
  useEffect(() => {
    if (deleting) dialog.current?.showModal();
    else dialog.current?.close();
  }, [deleting]);
  const refreshOne = useCallback(
    (id: string) => {
      const signal = lifetime.current.signal;
      void api
        .document(kb.id, id, signal)
        .then((doc) => {
          setItems((old) =>
            old.map((item) => (item.document_id === id ? doc : item)),
          );
          setPreviewDoc((old) => (old?.document_id === id ? doc : old));
        })
        .catch((reason) => {
          if (!cancelled(reason)) setError(reason);
        });
    },
    [kb.id],
  );
  async function action(run: (signal: AbortSignal) => Promise<void>) {
    if (actionBusy.current) return;
    actionBusy.current = true;
    setBusy(true);
    setError(null);
    setNotice("");
    const signal = lifetime.current.signal;
    try {
      await run(signal);
    } catch (reason) {
      if (!cancelled(reason)) setError(reason);
    } finally {
      actionBusy.current = false;
      if (!signal.aborted) setBusy(false);
    }
  }
  function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const file = new FormData(form).get("file");
    if (!(file instanceof File) || !file.size) {
      setError(new ApiError("请选择非空文件。"));
      return;
    }
    if (!/\.(md|txt|pdf)$/i.test(file.name)) {
      setError(new ApiError("仅支持 .md、.txt、.pdf 文件。"));
      return;
    }
    if (file.size > 10 * 1024 * 1024) {
      setError(new ApiError("文件超过 10 MiB，请选择较小的文件。"));
      return;
    }
    void action(async (signal) => {
      await api.upload(kb.id, file, signal);
      form.reset();
      setNotice("上传已接收。相同文件会复用已有记录；请查看任务状态。");
      setOffset(0);
      setReload((n) => n + 1);
    });
  }
  return (
    <section className="documents" aria-label="文档管理">
      <div className="document-nav">
        <button className="text-button" onClick={onBack}>
          ← 返回知识库
        </button>
        <label htmlFor="document-kb">
          当前知识库
          <select
            id="document-kb"
            aria-label="当前知识库"
            value={kb.id}
            onChange={(event) => onSelect(event.target.value)}
          >
            {bases.map((base) => (
              <option key={base.id} value={base.id}>
                {base.name}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="page-title">
        <div>
          <p className="eyebrow">LIBRARY DOCUMENTS</p>
          <h1>
            文档资料 <span className="count">{loading ? "—" : total}</span>
          </h1>
          <p className="muted">
            {kb.name} · {admin ? "管理员" : "成员 · 只读"}
          </p>
        </div>
        <button
          className="secondary"
          disabled={loading || busy}
          onClick={() => setReload((n) => n + 1)}
        >
          刷新文档
        </button>
      </div>
      {admin ? (
        <form className="upload-panel" onSubmit={upload} aria-busy={busy}>
          <div>
            <h2>添加团队资料</h2>
            <p id="upload-hint">
              支持 .md、.txt、文本型 .pdf，单文件最多 10 MiB。文本使用
              UTF-8；暂不支持扫描件 OCR。
            </p>
          </div>
          <label className="file-picker">
            选择文件
            <input
              name="file"
              type="file"
              accept=".md,.txt,.pdf"
              aria-describedby="upload-hint"
              disabled={busy}
            />
          </label>
          <button className="primary" disabled={busy}>
            {busy ? "正在提交…" : "上传文件"}
          </button>
        </form>
      ) : (
        <p className="notice">
          你可以查看文档与切块预览；上传、重建和删除由管理员操作。
        </p>
      )}
      <ErrorNotice error={error} />
      {notice && (
        <p className="notice" role="status">
          {notice}
        </p>
      )}
      {loading ? (
        <div className="loading-panel" role="status">
          <span className="spinner" />
          正在加载文档…
        </div>
      ) : !items.length && !error ? (
        <div className="empty-state">
          <span className="empty-icon" aria-hidden="true">
            ▤
          </span>
          <h2>还没有文档</h2>
          <p>
            {admin
              ? "上传第一份资料，后台会依次解析和建立索引。"
              : "管理员上传资料后会显示在这里。"}
          </p>
        </div>
      ) : (
        <div className="document-list">
          {items.map((doc) => (
            <article className="document-row" key={doc.document_id}>
              <div className="document-info">
                <span className="file-kind" aria-hidden="true">
                  {doc.file_name.split(".").pop()?.toUpperCase()}
                </span>
                <div>
                  <h2>{doc.file_name}</h2>
                  <small className="muted">
                    上传于 {new Date(doc.created_at).toLocaleString("zh-CN")}
                  </small>
                  <small className="document-id">文档 {doc.document_id}</small>
                </div>
              </div>
              <div className="document-state">
                <span className={`status-badge ${doc.status}`}>
                  {labels[doc.status]}
                </span>
                {admin && doc.latest_job && (
                  <JobStatus
                    key={doc.latest_job.job_id}
                    kb={kb.id}
                    doc={doc.document_id}
                    initial={doc.latest_job}
                    onTerminal={refreshOne}
                  />
                )}
                {doc.status === "ready" &&
                  doc.latest_job &&
                  doc.latest_job.status !== "succeeded" && (
                    <small className="muted">
                      当前仍使用已发布的有效构建。
                    </small>
                  )}
              </div>
              <div className="document-actions">
                <button
                  className="secondary"
                  onClick={() => setPreviewDoc(doc)}
                >
                  切块预览
                </button>
                {admin && (
                  <>
                    <button
                      className="text-button"
                      disabled={
                        busy || !!(doc.latest_job && active(doc.latest_job))
                      }
                      onClick={() =>
                        void action(async (signal) => {
                          await api.rebuild(kb.id, doc.document_id, signal);
                          setNotice("重建已提交，成功发布前保留旧的有效构建。");
                          refreshOne(doc.document_id);
                        })
                      }
                    >
                      重建索引
                    </button>
                    <button
                      className="text-button danger"
                      disabled={busy}
                      onClick={() => setDeleting(doc)}
                    >
                      删除文档
                    </button>
                  </>
                )}
              </div>
            </article>
          ))}
        </div>
      )}
      {!loading && total > 0 && (
        <nav className="pagination" aria-label="文档分页">
          <span>
            第 {Math.floor(offset / 10) + 1} /{" "}
            {Math.max(1, Math.ceil(total / 10))} 页 · 共 {total} 份
          </span>
          <button
            className="secondary"
            disabled={!offset || busy}
            onClick={() => setOffset((n) => n - 10)}
          >
            上一页
          </button>
          <button
            className="secondary"
            disabled={offset + 10 >= total || busy}
            onClick={() => setOffset((n) => n + 10)}
          >
            下一页
          </button>
        </nav>
      )}
      {previewDoc && (
        <section className="preview-panel" aria-label="切块预览">
          <div className="preview-heading">
            <div>
              <p className="eyebrow">PUBLISHED CHUNKS</p>
              <h2>{previewDoc.file_name}</h2>
            </div>
            <button className="text-button" onClick={() => setPreviewDoc(null)}>
              关闭预览
            </button>
          </div>
          <p className="muted">
            仅显示已发布构建的前 3 块，每块最多 600 字。预览不代表完整原文。
          </p>
          <ErrorNotice error={previewError} />
          {!preview && !previewError && <p role="status">正在读取预览…</p>}
          {preview && (
            <>
              {!preview.items.length ? (
                <p>尚无已发布切块，入库完成后可查看。</p>
              ) : (
                <>
                  <small className="document-id">
                    构建 {preview.build_id} · 共 {preview.total_chunks} 块
                  </small>
                  {preview.items.map((chunk) => (
                    <article className="chunk-preview" key={chunk.chunk_id}>
                      <h3>
                        片段 {chunk.ordinal + 1}
                        {chunk.page_number !== null
                          ? ` · 第 ${chunk.page_number} 页`
                          : chunk.start_line !== null
                            ? ` · 第 ${chunk.start_line}–${chunk.end_line ?? chunk.start_line} 行`
                            : ""}
                      </h3>
                      {chunk.heading_path.length > 0 && (
                        <p className="muted">
                          {chunk.heading_path.join(" / ")}
                          {chunk.locator_truncated ? "（标题已截断）" : ""}
                        </p>
                      )}
                      <pre>{chunk.text}</pre>
                      {chunk.truncated && <small>此片段已截断。</small>}
                    </article>
                  ))}
                </>
              )}
            </>
          )}
        </section>
      )}
      <dialog
        ref={dialog}
        className="delete-dialog"
        onCancel={(event) => {
          event.preventDefault();
          if (!busy) setDeleting(null);
        }}
      >
        <h2>确认删除文档？</h2>
        <p>
          将删除「{deleting?.file_name}
          」。删除后不可检索，正在进行的任务也不能重新发布该文档。此操作不可撤销。
        </p>
        <div className="dialog-actions">
          <button
            className="secondary"
            disabled={busy}
            onClick={() => setDeleting(null)}
          >
            取消
          </button>
          <button
            className="primary danger-button"
            disabled={busy}
            onClick={() => {
              if (deleting)
                void action(async (signal) => {
                  const result = await api.deleteDocument(
                    kb.id,
                    deleting.document_id,
                    signal,
                  );
                  setDeleting(null);
                  setPreviewDoc(null);
                  setReload((n) => n + 1);
                  setNotice(
                    result.cleanup_status === "blocked" ||
                      result.cleanup_status === "pending"
                      ? "文档已删除并停止检索；原文件清理尚未完成，请联系管理员。"
                      : "文档已删除。",
                  );
                });
            }}
          >
            确认删除
          </button>
        </div>
        {busy && <p role="status">正在删除…</p>}
        <ErrorNotice error={error} />
      </dialog>
    </section>
  );
}
