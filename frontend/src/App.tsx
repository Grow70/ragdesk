import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import type { FormEvent } from "react";
import { api, ApiError, cancelled } from "./api";
import type { CurrentUser, KnowledgeBase } from "./api";

function Brand() {
  return (
    <div className="brand">
      <span className="brand-mark" aria-hidden="true">
        r.
      </span>
      <span>
        ragdesk<span className="brand-caption">知识工作空间</span>
      </span>
    </div>
  );
}
function ErrorNotice({ error }: { error: unknown }) {
  if (!error) return null;
  return (
    <div className="error" role="alert">
      <strong>
        {error instanceof ApiError ? error.message : "操作未完成，请重试。"}
      </strong>
      {error instanceof ApiError && error.requestId && (
        <small>请求编号：{error.requestId}</small>
      )}
    </div>
  );
}
function Login({ notice }: { notice: string }) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const submitting = useRef(false);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting.current) return;
    const form = event.currentTarget;
    const values = new FormData(form);
    const login = String(values.get("login_name") ?? "").trim();
    const password = String(values.get("password") ?? "");
    if (!login || !password) {
      setError(new ApiError("请填写账号和密码。"));
      return;
    }
    submitting.current = true;
    setPending(true);
    setError(null);
    (form.elements.namedItem("password") as HTMLInputElement).value = "";
    try {
      await api.login(login, password);
    } catch (reason) {
      if (!cancelled(reason)) setError(reason);
    } finally {
      submitting.current = false;
      setPending(false);
    }
  }
  return (
    <main className="login-layout">
      <section className="welcome" aria-labelledby="welcome-title">
        <Brand />
        <div className="welcome-copy">
          <p className="eyebrow">TEAM KNOWLEDGE, ONE PLACE</p>
          <h1 id="welcome-title">
            知识有归处。
            <br />
            工作有依据。
          </h1>
          <p className="welcome-description">
            从团队共享的知识开始，
            <br />
            进入属于你的工作空间。
          </p>
          <div className="paper-stack" aria-hidden="true">
            <div className="paper-back" />
            <div className="paper-front">
              <span className="paper-label">KNOWLEDGE LIBRARY</span>
              <span className="paper-title">团队知识档案</span>
              <i />
              <i />
              <i />
              <span className="paper-stamp">R / D</span>
            </div>
          </div>
        </div>
        <p className="welcome-foot">
          RAGDESK <span>企业知识库 · 演示版</span>
        </p>
      </section>
      <section className="login-panel" aria-labelledby="login-title">
        <span className="edition">
          工作空间入口 <span aria-hidden="true">↗</span>
        </span>
        <div className="login-card">
          <p className="eyebrow">WELCOME BACK</p>
          <h2 id="login-title">登录 Ragdesk</h2>
          <p className="muted">使用管理员为你开通的账号登录。</p>
          {notice && (
            <p className="notice" role="status">
              {notice}
            </p>
          )}
          <ErrorNotice error={error} />
          <form onSubmit={submit} aria-busy={pending}>
            <label htmlFor="login-name">账号</label>
            <input
              id="login-name"
              name="login_name"
              autoComplete="username"
              maxLength={100}
              placeholder="输入你的账号"
              required
              disabled={pending}
            />
            <label htmlFor="password">密码</label>
            <input
              id="password"
              name="password"
              type="password"
              autoComplete="current-password"
              placeholder="输入密码"
              required
              disabled={pending}
            />
            <button className="primary login-button" disabled={pending}>
              {pending ? "正在验证身份…" : "登录工作空间"}
              <span aria-hidden="true">→</span>
            </button>
          </form>
          <p className="login-help">还没有账号？请联系工作空间管理员。</p>
        </div>
        <p className="session-note">
          <span aria-hidden="true">◈</span>{" "}
          仅在当前页面保持登录，刷新后需重新登录。
        </p>
      </section>
    </main>
  );
}

function Workspace() {
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [bases, setBases] = useState<KnowledgeBase[]>([]);
  const [selected, setSelected] = useState<KnowledgeBase | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [choosing, setChoosing] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const selectionRun = useRef(0);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      selectionRun.current += 1;
    };
  }, []);
  useEffect(() => {
    let current = true;
    selectionRun.current += 1;
    setLoading(true);
    setError(null);
    setSelected(null);
    setChoosing(null);
    setBases([]);
    void Promise.all([api.me(), api.knowledgeBases()])
      .then(([identity, items]) => {
        if (current) {
          setUser(identity);
          setBases(items);
        }
      })
      .catch((reason: unknown) => {
        if (current && !cancelled(reason)) setError(reason);
      })
      .finally(() => {
        if (current) setLoading(false);
      });
    return () => {
      current = false;
    };
  }, [reload]);

  async function choose(id: string) {
    const run = ++selectionRun.current;
    setSelected(null);
    setChoosing(id);
    setError(null);
    try {
      const value = await api.knowledgeBase(id);
      if (mounted.current && run === selectionRun.current) setSelected(value);
    } catch (reason) {
      if (
        mounted.current &&
        run === selectionRun.current &&
        !cancelled(reason)
      ) {
        setError(reason);
        if (
          reason instanceof ApiError &&
          (reason.status === 404 || reason.status === 403)
        ) {
          setBases((items) => items.filter((item) => item.id !== id));
        }
      }
    } finally {
      if (mounted.current && run === selectionRun.current) setChoosing(null);
    }
  }
  return (
    <div className="workspace">
      <aside className="sidebar">
        <Brand />
        <div className="sidebar-section">
          <p className="eyebrow">工作空间</p>
          <div className="nav-active">
            <span aria-hidden="true">▤</span> 我的知识库
          </div>
        </div>
        <div className="sidebar-bottom">
          <span className="demo-badge">演示工作空间</span>
          <p>
            每一份知识，
            <br />
            都有清晰的归属。
          </p>
          <div className="identity">
            <span className="avatar" aria-hidden="true">
              {user?.display_name.slice(0, 1) || "·"}
            </span>
            <div>
              <strong>{user?.display_name || "正在确认身份"}</strong>
              <small>{user?.login_name || "请稍候"}</small>
            </div>
          </div>
          <button
            className="logout"
            onClick={() => api.logout("你已退出登录。")}
          >
            退出登录 <span aria-hidden="true">↗</span>
          </button>
        </div>
      </aside>
      <main className="workspace-main">
        <header className="topbar">
          <span>
            工作空间 <span className="slash">/</span> 知识库
          </span>
          <span className="private-label">
            <i /> 授权访问
          </span>
        </header>
        <div className="workspace-content">
          <div className="page-title">
            <div>
              <p className="eyebrow">YOUR LIBRARIES</p>
              <h1>
                我的知识库
                <span className="count">{loading ? "—" : bases.length}</span>
              </h1>
              <p className="muted">选择一个知识库，确定本次工作的资料范围。</p>
            </div>
            <button
              className="secondary"
              disabled={loading}
              onClick={() => setReload((value) => value + 1)}
            >
              {loading ? "加载中…" : "刷新列表"}{" "}
              <span aria-hidden="true">↻</span>
            </button>
          </div>
          <ErrorNotice error={error} />
          {loading ? (
            <div className="loading-panel" role="status">
              <span className="spinner" />
              正在加载你的知识库…
            </div>
          ) : (
            <>
              {!bases.length && !error && (
                <div className="empty-state">
                  <span className="empty-icon" aria-hidden="true">
                    ▤
                  </span>
                  <h2>还没有可访问的知识库</h2>
                  <p>请联系管理员将你加入知识库，然后刷新列表。</p>
                </div>
              )}
              <div className="library-grid" aria-label="可访问的知识库">
                {bases.map((kb, index) => (
                  <button
                    key={kb.id}
                    className={`library-card ${selected?.id === kb.id ? "is-selected" : ""}`}
                    aria-pressed={selected?.id === kb.id}
                    onClick={() => void choose(kb.id)}
                    disabled={choosing === kb.id}
                  >
                    <div className="card-top">
                      <span className="library-icon" aria-hidden="true">
                        ▤
                      </span>
                      <span className={`role-badge ${kb.role}`}>
                        {kb.role === "admin" ? "管理员" : "成员"}
                      </span>
                    </div>
                    <span className="card-number">
                      LIBRARY / {String(index + 1).padStart(2, "0")}
                    </span>
                    <strong className="library-name">{kb.name}</strong>
                    <span className="card-bottom">
                      <span>
                        {choosing === kb.id
                          ? "正在确认访问权限…"
                          : selected?.id === kb.id
                            ? "已选择"
                            : "选择知识库"}
                      </span>
                      <span aria-hidden="true">
                        {selected?.id === kb.id ? "✓" : "↗"}
                      </span>
                    </span>
                  </button>
                ))}
              </div>
              {selected && (
                <section
                  className="selection"
                  aria-labelledby="selection-title"
                >
                  <div className="selection-mark" aria-hidden="true">
                    ✓
                  </div>
                  <div>
                    <p className="eyebrow">当前知识库</p>
                    <h2 id="selection-title">{selected.name}</h2>
                    <p>
                      你的角色：{selected.role === "admin" ? "管理员" : "成员"}
                      <span className="dot">·</span>创建于{" "}
                      {new Date(selected.created_at).toLocaleDateString(
                        "zh-CN",
                      )}
                    </p>
                  </div>
                  <span className="selected-label" role="status">
                    已选择
                  </span>
                </section>
              )}
            </>
          )}
          <footer className="workspace-foot">
            <span>仅展示你有权访问的知识库。</span>
            <span>RAGDESK / DEMO</span>
          </footer>
        </div>
      </main>
    </div>
  );
}

export default function App() {
  const session = useSyncExternalStore(api.subscribe, api.getSnapshot);
  return session.authenticated ? (
    <Workspace />
  ) : (
    <Login notice={session.notice} />
  );
}
