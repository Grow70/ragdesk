/** Mirrors FastAPI's current root-path contracts; token never leaves this instance. */
export interface CurrentUser {
  user_id: string;
  login_name: string;
  display_name: string;
  request_id: string;
}
export interface KnowledgeBase {
  id: string;
  name: string;
  role: "admin" | "member";
  created_at: string;
  request_id: string;
}
export interface IngestionJob {
  job_id: string;
  status: "queued" | "running" | "succeeded" | "failed";
  attempts: number;
  max_attempts: number;
  error_code: string | null;
  error_summary: string | null;
}
export interface DocumentItem {
  document_id: string;
  file_name: string;
  status: "uploaded" | "queued" | "processing" | "ready" | "failed";
  created_at: string;
  latest_job: IngestionJob | null;
}
export interface ChunkPreview {
  build_id: string | null;
  total_chunks: number;
  items: {
    chunk_id: string;
    ordinal: number;
    text: string;
    truncated: boolean;
    page_number: number | null;
    heading_path: string[];
    start_line: number | null;
    end_line: number | null;
    locator_truncated: boolean;
  }[];
}
const integer = (value: unknown): number => {
  if (!Number.isSafeInteger(value) || (value as number) < 0)
    throw new Error("Invalid number");
  return value as number;
};
function job(value: unknown): IngestionJob {
  const d = record(value);
  if (!["queued", "running", "succeeded", "failed"].includes(String(d.status)))
    throw new Error("Invalid job");
  return {
    job_id: uuid(d.job_id),
    status: d.status as IngestionJob["status"],
    attempts: integer(d.attempts),
    max_attempts: integer(d.max_attempts),
    error_code: d.error_code === null ? null : text(d.error_code),
    error_summary: d.error_summary === null ? null : text(d.error_summary),
  };
}
function documentItem(value: unknown): DocumentItem {
  const d = record(value);
  if (
    !["uploaded", "queued", "processing", "ready", "failed"].includes(
      String(d.status),
    )
  )
    throw new Error("Invalid document");
  return {
    document_id: uuid(d.document_id),
    file_name: text(d.file_name, 255),
    status: d.status as DocumentItem["status"],
    created_at: text(d.created_at),
    latest_job: d.latest_job == null ? null : job(d.latest_job),
  };
}
const documentPath = (kb: string, id?: string) =>
  `/knowledge-bases/${encodeURIComponent(uuid(kb))}/documents${id ? "/" + encodeURIComponent(uuid(id)) : ""}`;

interface Session {
  authenticated: boolean;
  notice: string;
}

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status = 0,
    public readonly requestId?: string,
    public readonly code = "REQUEST_FAILED",
  ) {
    super(message);
  }
}
export const cancelled = (error: unknown) =>
  error instanceof ApiError && error.code === "CANCELLED";

const record = (value: unknown): Record<string, unknown> => {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("Invalid response shape");
  }
  return value as Record<string, unknown>;
};
const text = (value: unknown, max = 200): string => {
  if (typeof value !== "string" || !value || value.length > max) {
    throw new Error("Invalid response field");
  }
  return value;
};
const uuid = (value: unknown) => {
  const id = text(value, 36);
  if (!/^[\da-f]{8}(-[\da-f]{4}){3}-[\da-f]{12}$/i.test(id)) {
    throw new Error("Invalid identifier");
  }
  return id;
};
function base(value: unknown): KnowledgeBase {
  const data = record(value);
  if (data.role !== "admin" && data.role !== "member")
    throw new Error("Invalid role");
  const created_at = text(data.created_at);
  if (!Number.isFinite(Date.parse(created_at))) throw new Error("Invalid date");
  return {
    id: uuid(data.id),
    name: text(data.name),
    role: data.role,
    created_at,
    request_id: text(data.request_id, 128),
  };
}
function failure(status: number, login: boolean): string {
  if (status === 401)
    return login ? "账号或密码错误，请重试。" : "登录已过期，请重新登录。";
  if (status === 403) return "你没有执行此操作的权限。";
  if (status === 404) return "知识库不存在或你已无权访问，请刷新列表。";
  if (status === 413) return "文件超过 10 MiB，请选择较小的文件。";
  if (status === 409)
    return "当前模型配置与有效索引不兼容，请联系管理员检查后端配置。";
  if (status === 400) return "文件无效，请检查文件名、编码和实际文件格式。";
  if (status === 422) return "输入格式不正确，请检查后重试。";
  if (status === 429) return "请求过于频繁，请稍后重试。";
  if (status >= 500) return "服务暂时不可用，请稍后重试。";
  return "请求未完成，请稍后重试。";
}

export class ApiClient {
  #token: string | null = null;
  #expiresAt = 0;
  #revision = 0;
  #timer: ReturnType<typeof setTimeout> | undefined;
  #requests = new Set<AbortController>();
  #listeners = new Set<() => void>();
  #session: Session = { authenticated: false, notice: "" };

  getSnapshot = () => this.#session;
  subscribe = (listener: () => void) => {
    this.#listeners.add(listener);
    if (this.#listeners.size === 1) {
      window.addEventListener("focus", this.checkExpiry);
      document.addEventListener("visibilitychange", this.checkExpiry);
    }
    return () => {
      this.#listeners.delete(listener);
      if (!this.#listeners.size) {
        window.removeEventListener("focus", this.checkExpiry);
        document.removeEventListener("visibilitychange", this.checkExpiry);
      }
    };
  };
  #notify(session: Session) {
    this.#session = session;
    this.#listeners.forEach((listener) => listener());
  }
  logout = (notice = "") => {
    this.#revision += 1;
    this.#token = null;
    this.#expiresAt = 0;
    clearTimeout(this.#timer);
    this.#requests.forEach((controller) => controller.abort());
    this.#requests.clear();
    this.#notify({ authenticated: false, notice });
  };
  checkExpiry = () => {
    if (this.#token && Date.now() >= this.#expiresAt) {
      this.logout("登录已过期，请重新登录。");
    }
  };

  async #request<T>(
    path: string,
    decode: (value: unknown) => T,
    body?: object,
    options: { method?: string; signal?: AbortSignal } = {},
  ): Promise<T> {
    // Paths are private constants; never send a Bearer credential to an arbitrary URL.
    const login = path === "/auth/session";
    this.checkExpiry();
    if (!login && !this.#token)
      throw new ApiError("请先登录。", 401, undefined, "CANCELLED");
    const revision = this.#revision;
    const controller = new AbortController();
    const abort = () => controller.abort();
    options.signal?.addEventListener("abort", abort, { once: true });
    if (options.signal?.aborted) controller.abort();
    this.#requests.add(controller);
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, 15_000);
    let requestId: string | undefined;
    try {
      const headers: Record<string, string> = { Accept: "application/json" };
      if (body && !(body instanceof FormData))
        headers["Content-Type"] = "application/json";
      if (!login) headers.Authorization = `Bearer ${this.#token}`;
      const response = await fetch(`/api${path}`, {
        method: options.method ?? (body ? "POST" : "GET"),
        headers,
        body:
          body instanceof FormData
            ? body
            : body
              ? JSON.stringify(body)
              : undefined,
        signal: controller.signal,
        credentials: "omit",
        cache: "no-store",
        redirect: "error",
      });
      let payload: unknown;
      try {
        payload = await response.json();
      } catch {
        payload = undefined;
      }
      if (revision !== this.#revision || options.signal?.aborted)
        throw new ApiError("", 0, undefined, "CANCELLED");
      const headerId = response.headers.get("X-Request-ID");
      if (headerId && /^[\w-]{1,128}$/.test(headerId)) requestId = headerId;
      if (
        !requestId &&
        payload &&
        typeof payload === "object" &&
        "request_id" in payload
      ) {
        const id = payload.request_id;
        if (typeof id === "string" && /^[\w-]{1,128}$/.test(id)) requestId = id;
      }
      if (!response.ok) {
        if (response.status === 401 && !login)
          this.logout("登录已过期或失效，请重新登录。");
        throw new ApiError(
          failure(response.status, login),
          response.status,
          requestId,
        );
      }
      if (timedOut)
        throw new ApiError("请求超时，请重试。", 0, requestId, "TIMEOUT");
      try {
        return decode(payload);
      } catch {
        throw new ApiError(
          "服务返回了无法识别的数据，请稍后重试。",
          response.status,
          requestId,
          "INVALID_RESPONSE",
        );
      }
    } catch (error) {
      if (revision !== this.#revision || options.signal?.aborted)
        throw new ApiError("", 0, undefined, "CANCELLED");
      if (error instanceof ApiError) throw error;
      throw new ApiError(
        timedOut
          ? "请求超时，请重试。"
          : "无法连接服务，请检查网络或后端是否启动。",
        0,
        requestId,
        timedOut ? "TIMEOUT" : "NETWORK",
      );
    } finally {
      clearTimeout(timer);
      options.signal?.removeEventListener("abort", abort);
      this.#requests.delete(controller);
    }
  }

  async login(login_name: string, password: string) {
    this.logout();
    const revision = this.#revision;
    const started = Date.now();
    const result = await this.#request(
      "/auth/session",
      (value) => {
        const data = record(value);
        if (
          data.token_type !== "bearer" ||
          !Number.isInteger(data.expires_in) ||
          (data.expires_in as number) <= 0 ||
          (data.expires_in as number) > 86_400
        ) {
          throw new Error("Invalid session");
        }
        return {
          token: text(data.access_token, 8192),
          seconds: data.expires_in as number,
        };
      },
      { login_name, password },
    );
    if (revision !== this.#revision)
      throw new ApiError("", 0, undefined, "CANCELLED");
    // JWT exp is second-granular. A conservative second avoids overstating TTL.
    this.#expiresAt = started + result.seconds * 1000 - 1000;
    if (Date.now() >= this.#expiresAt)
      throw new ApiError("登录响应已过期，请重试。");
    this.#token = result.token;
    this.#timer = setTimeout(this.checkExpiry, this.#expiresAt - Date.now());
    this.#notify({ authenticated: true, notice: "" });
  }
  me() {
    return this.#request("/auth/me", (value): CurrentUser => {
      const data = record(value);
      return {
        user_id: uuid(data.user_id),
        login_name: text(data.login_name, 100),
        display_name: text(data.display_name),
        request_id: text(data.request_id, 128),
      };
    });
  }
  knowledgeBases() {
    return this.#request("/knowledge-bases", (value) => {
      const data = record(value);
      if (!Array.isArray(data.items)) throw new Error("Invalid list");
      return data.items.map(base);
    });
  }
  knowledgeBase(id: string) {
    return this.#request(`/knowledge-bases/${encodeURIComponent(id)}`, base);
  }
  documents(kb: string, offset: number, signal: AbortSignal) {
    return this.#request(
      `${documentPath(kb)}?limit=10&offset=${integer(offset)}`,
      (value) => {
        const d = record(value);
        if (!Array.isArray(d.items) || d.items.length > 10)
          throw new Error("Invalid list");
        return { items: d.items.map(documentItem), total: integer(d.total) };
      },
      undefined,
      { signal },
    );
  }
  document(kb: string, id: string, signal: AbortSignal) {
    return this.#request(documentPath(kb, id), documentItem, undefined, {
      signal,
    });
  }
  job(kb: string, id: string, jobId: string, signal: AbortSignal) {
    return this.#request(
      `${documentPath(kb, id)}/jobs/${encodeURIComponent(uuid(jobId))}`,
      job,
      undefined,
      { signal },
    );
  }
  upload(kb: string, file: File, signal: AbortSignal) {
    const body = new FormData();
    body.append("file", file);
    return this.#request(documentPath(kb), accepted, body, { signal });
  }
  rebuild(kb: string, id: string, signal: AbortSignal) {
    return this.#request(
      `${documentPath(kb, id)}/rebuild`,
      accepted,
      undefined,
      { method: "POST", signal },
    );
  }
  deleteDocument(kb: string, id: string, signal: AbortSignal) {
    return this.#request(
      documentPath(kb, id),
      (value) => {
        const d = record(value);
        if (
          d.status !== "deleted" ||
          uuid(d.document_id) !== id ||
          !["removed", "missing", "blocked", "pending"].includes(
            String(d.cleanup_status),
          )
        )
          throw new Error("Invalid deletion");
        return { cleanup_status: String(d.cleanup_status) };
      },
      undefined,
      { method: "DELETE", signal },
    );
  }
  preview(kb: string, id: string, signal: AbortSignal) {
    return this.#request(
      `${documentPath(kb, id)}/preview`,
      (value): ChunkPreview => {
        const d = record(value);
        if (
          uuid(d.document_id) !== id ||
          !Array.isArray(d.items) ||
          d.items.length > 3
        )
          throw new Error("Invalid preview");
        return {
          build_id: d.build_id === null ? null : uuid(d.build_id),
          total_chunks: integer(d.total_chunks),
          items: d.items.map((value) => {
            const c = record(value);
            if (
              !Array.isArray(c.heading_path) ||
              c.heading_path.length > 6 ||
              typeof c.truncated !== "boolean" ||
              typeof c.locator_truncated !== "boolean"
            )
              throw new Error("Invalid chunk");
            return {
              chunk_id: uuid(c.chunk_id),
              ordinal: integer(c.ordinal),
              text: text(c.text, 600),
              truncated: c.truncated,
              locator_truncated: c.locator_truncated,
              page_number:
                c.page_number === null ? null : integer(c.page_number),
              heading_path: c.heading_path.map((h) => text(h, 160)),
              start_line: c.start_line === null ? null : integer(c.start_line),
              end_line: c.end_line === null ? null : integer(c.end_line),
            };
          }),
        };
      },
      undefined,
      { signal },
    );
  }
}

function accepted(value: unknown) {
  const d = record(value);
  if (
    d.status !== "uploaded" ||
    !["queued", "running", "succeeded", "failed"].includes(String(d.job_status))
  )
    throw new Error("Invalid acceptance");
  return { document_id: uuid(d.document_id), job_id: uuid(d.job_id) };
}

export const api = new ApiClient();
