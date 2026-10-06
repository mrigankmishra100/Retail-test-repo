import { useEffect, useId, useRef, useState } from 'react';
export function Icon({ name = 'grid', size = 20, ...props }) {
  const paths = {
    grid: (
      <>
        <rect x="3" y="3" width="7" height="7" rx="1" />
        <rect x="14" y="3" width="7" height="7" rx="1" />
        <rect x="3" y="14" width="7" height="7" rx="1" />
        <rect x="14" y="14" width="7" height="7" rx="1" />
      </>
    ),
    box: <path d="m12 3 9 5v9l-9 5-9-5V8l9-5Zm0 10v9M3 8l9 5 9-5M7 5.8l9 5" />,
    flow: (
      <>
        <rect x="3" y="3" width="7" height="6" rx="1" />
        <rect x="14" y="15" width="7" height="6" rx="1" />
        <path d="M6 9v9h8m4-12v7m-3-3 3 3 3-3" />
      </>
    ),
    check: <path d="m5 12 4 4L19 6" />,
    shield: (
      <>
        <path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3Z" />
        <path d="m8 12 3 3 5-6" />
      </>
    ),
    people: (
      <>
        <circle cx="9" cy="7" r="3" />
        <path d="M3 21v-4a6 6 0 0 1 12 0v4m2-17a3 3 0 0 1 0 6m1 4a5 5 0 0 1 3 5v2" />
      </>
    ),
    bot: <><rect x="4" y="7" width="16" height="14" rx="5" /><path d="M12 7V3m-2 0h4M1 12v5m22-5v5M9 17h6" /><path className="bot-eyes" d="M8 12v1m8-1v1" /></>,
    chat: <path d="M4 4h16v13H9l-5 4V4Zm4 5h8m-8 4h5" />,
    arrow: <path d="M5 12h14m-5-5 5 5-5 5" />,
    refresh: <path d="M20 10a8 8 0 1 0-1 8M20 4v6h-6" />,
    search: (
      <>
        <circle cx="10" cy="10" r="6" />
        <path d="m15 15 6 6" />
      </>
    ),
    close: <path d="m6 6 12 12M6 18 18 6" />,
    chevron: <path d="m9 5 7 7-7 7" />,
    pulse: <path d="M2 12h5l3-8 4 16 3-8h5" />,
    alert: <path d="m12 3 10 18H2L12 3Zm0 5v6m0 3v1" />,
    clock: (
      <>
        <circle cx="12" cy="12" r="9" />
        <path d="M12 6v6l4 2" />
      </>
    ),
    spark: <path d="m12 2 3 7 7 3-7 3-3 7-3-7-7-3 7-3 3-7Zm7 0v4m-2-2h4" />,
    logout: <path d="M9 4H4v16h5m0-8h12m-5-5 5 5-5 5" />,
    menu: <path d="M3 6h18M3 12h18M3 18h18" />,
    file: <path d="M5 3h9l5 5v13H5V3Zm9 0v6h5M8 13h8m-8 4h6" />,
  };
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...props}
    >
      {paths[name] || paths.grid}
    </svg>
  );
}
export const human = (text) =>
  (text || 'Unknown')
    .toLowerCase()
    .replaceAll('_', ' ')
    .replace(/^./, (c) => c.toUpperCase());
export const number = (value) =>
  value == null ? '—' : new Intl.NumberFormat('en-IN', { maximumFractionDigits: 1 }).format(value);
export const money = (value) =>
  value == null
    ? 'Not priced'
    : new Intl.NumberFormat('en-IN', {
        style: 'currency',
        currency: 'INR',
        maximumFractionDigits: 2,
      }).format(Number(value));
export function Badge({ children, tone = 'neutral' }) {
  return <span className={`badge ${tone}`}>{children}</span>;
}
export function Status({ value }) {
  const tone =
    /rejected|at_risk|failed|blocked|insufficient|not_ready|unavailable/.test(
      value?.toLowerCase(),
    ) && value !== 'not_at_risk'
      ? 'danger'
      : /awaiting|pending|queued|missing|reconciliation/.test(value?.toLowerCase())
        ? 'warning'
        : /completed|sent|responded|not_at_risk|ready|approved|no_risk/.test(value?.toLowerCase())
          ? 'success'
          : 'neutral';
  return <Badge tone={tone}>{human(value)}</Badge>;
}
export function ErrorNotice({ error, retry }) {
  return error ? (
    <div className="error-notice" role="alert">
      <Icon name="alert" />
      <div>
        <strong>{error.message || String(error)}</strong>
        {error.status === 409 && (
          <p>
            Refresh and review the current case. If it is unchanged, check the notification
            configuration or changed supplier terms.
          </p>
        )}
        {error.traceId && <small>Trace ID: {error.traceId}</small>}
      </div>
      {retry && <button onClick={retry}>Retry</button>}
    </div>
  ) : null;
}
export function Empty({ title, children, icon = 'box' }) {
  return (
    <div className="empty">
      <span className="empty-icon">
        <Icon name={icon} size={28} />
      </span>
      <h3>{title}</h3>
      <p>{children}</p>
    </div>
  );
}
export function Loading({ label = 'Loading workspace data…' }) {
  return (
    <div className="loading" role="status">
      <span className="spinner" />
      {label}
    </div>
  );
}
export function Panel({ title, subtitle, action, children, className = '' }) {
  return (
    <section className={`panel ${className}`}>
      <div className="panel-heading">
        <div>
          <h2>{title}</h2>
          {subtitle && <p>{subtitle}</p>}
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}
export function useResource(loader, deps = [], pollMs = 0) {
  const [state, setState] = useState({ data: null, loading: true, error: null });
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let active = true,
      timer;
    setState((s) => ({ ...s, loading: true, error: null }));
    async function fetchData(background = false) {
      try {
        if (!background || document.visibilityState !== 'hidden') {
          const data = await loader();
          if (active) setState({ data, loading: false, error: null });
        }
      } catch (error) {
        if (active) setState((s) => ({ data: background ? s.data : null, loading: false, error }));
      } finally {
        if (active && pollMs) timer = setTimeout(() => fetchData(true), pollMs);
      }
    }
    fetchData();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [...deps, revision, pollMs]);
  return { ...state, reload: () => setRevision((n) => n + 1) };
}
export function useAction() {
  const lock = useRef(false),
    keys = useRef(new Map());
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(null);
  async function run(identity, action) {
    if (lock.current) return;
    lock.current = true;
    setBusy(true);
    setError(null);
    if (!keys.current.has(identity)) keys.current.set(identity, crypto.randomUUID());
    try {
      const result = await action(keys.current.get(identity));
      keys.current.delete(identity);
      return result;
    } catch (e) {
      setError(e);
      return undefined;
    } finally {
      lock.current = false;
      setBusy(false);
    }
  }
  return { run, busy, error, clear: () => setError(null) };
}
export function Dialog({ title, onClose, children }) {
  const ref = useRef(null);
  const titleId = useId();
  useEffect(() => {
    const previous = document.activeElement;
    const dialog = ref.current;
    dialog.showModal();
    return () => {
      dialog.close();
      previous?.focus?.();
    };
  }, []);
  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      className="drawer"
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
    >
      <div className="drawer-heading">
        <h2 id={titleId}>{title}</h2>
        <button className="icon-button" aria-label="Close detail panel" onClick={onClose}>
          <Icon name="close" />
        </button>
      </div>
      <div className="drawer-body">{children}</div>
    </dialog>
  );
}
