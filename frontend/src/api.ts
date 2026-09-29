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
  ): Promise<T> {
    // Paths are private constants; never send a Bearer credential to an arbitrary URL.
    const login = path === "/auth/session";
    this.checkExpiry();
    if (!login && !this.#token)
      throw new ApiError("请先登录。", 401, undefined, "CANCELLED");
    const revision = this.#revision;
    const controller = new AbortController();
    this.#requests.add(controller);
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, 15_000);
    let requestId: string | undefined;
    try {
      const headers: Record<string, string> = { Accept: "application/json" };
      if (body) headers["Content-Type"] = "application/json";
      if (!login) headers.Authorization = `Bearer ${this.#token}`;
      const response = await fetch(`/api${path}`, {
        method: body ? "POST" : "GET",
        headers,
        body: body ? JSON.stringify(body) : undefined,
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
      if (revision !== this.#revision)
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
      if (revision !== this.#revision)
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
}

export const api = new ApiClient();
