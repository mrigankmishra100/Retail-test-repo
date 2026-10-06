import { useEffect, useState } from 'react';
import api, { setToken } from './api';
import { modules } from './modules';
import { Badge, ErrorNotice, Icon, Loading, useAction, useResource } from './ui';
import { Overview } from './pages/Workspace';
import AgentChat from './pages/AgentChat';
import RequestActivity from './pages/RequestActivity';

const SESSION = 'retail.session.v1';
function readSession() {
  try {
    return JSON.parse(sessionStorage.getItem(SESSION));
  } catch {
    return null;
  }
}
function Brand() {
  return (
    <div className="brand">
      <span className="oracle-wordmark">ORACLE</span>
      <span className="brand-divider" />
      <strong>Retail Inventory</strong>
    </div>
  );
}

function Login({ onLogin, expired }) {
  const [role, setRole] = useState('retail-manager'),
    [supplier, setSupplier] = useState('SUP001');
  const action = useAction();
  async function submit(e) {
    e.preventDefault();
    const result = await action.run('login', () =>
      api.login({ role, ...(role === 'supplier' ? { supplier_id: supplier } : {}) }),
    );
    if (result) onLogin(result);
  }
  return (
    <div className="login-page">
      <header className="topbar">
        <Brand />
        <Badge>Operations workspace</Badge>
      </header>
      <main className="login-main">
        <section className="login-story">
          <div className="eyebrow">INTELLIGENCE THAT KEEPS SHELVES READY</div>
          <h1>
            The right stock.
            <br />
            The right decisions.
          </h1>
          <p>Bring inventory, suppliers, and replenishment into one connected workspace.</p>
          <div className="story-features">
            {[
              ['box', 'See what needs attention', 'Understand stock risk and demand coverage.'],
              [
                'flow',
                'Move from insight to action',
                'Compare suppliers and prepare replenishment.',
              ],
              ['shield', 'Stay in control', 'Review every commercial decision before approval.'],
            ].map(([icon, title, body]) => (
              <div key={title}>
                <span>
                  <Icon name={icon} />
                </span>
                <section>
                  <h3>{title}</h3>
                  <p>{body}</p>
                </section>
              </div>
            ))}
          </div>
          <div className="story-bottom">
            RETAIL OPERATIONS <span>Powered by Oracle Database & LangGraph</span>
          </div>
        </section>
        <section className="login-card">
          <span className="app-mark">
            <Icon name="box" size={28} />
          </span>
          <h2>Welcome to your workspace</h2>
          <p>Select your role to start a server-managed POC session.</p>
          {expired && (
            <div className="callout">Your session has expired. Sign in again to continue.</div>
          )}
          <form onSubmit={submit}>
            <fieldset className="role-options">
              <legend>Workspace role</legend>
              {[
                ['retail-manager', 'Retail manager', 'Inventory, sourcing, and approvals', 'grid'],
                ['supplier', 'Supplier', 'Requests, quotes, and policy', 'people'],
              ].map(([value, label, description, icon]) => (
                <label
                  className={role === value ? 'role-option selected' : 'role-option'}
                  key={value}
                >
                  <input
                    type="radio"
                    name="role"
                    value={value}
                    checked={role === value}
                    onChange={() => setRole(value)}
                  />
                  <Icon name={icon} />
                  <span>
                    <strong>{label}</strong>
                    <small>{description}</small>
                  </span>
                </label>
              ))}
            </fieldset>
            {role === 'supplier' && (
              <label>
                Supplier ID
                <select value={supplier} onChange={(e) => setSupplier(e.target.value)}>
                  {['SUP001', 'SUP002', 'SUP003', 'SUP004'].map((id) => (
                    <option key={id}>{id}</option>
                  ))}
                </select>
              </label>
            )}
            <ErrorNotice error={action.error} />
            <button className="primary full" disabled={action.busy}>
              {action.busy ? 'Opening workspace…' : 'Enter workspace'}
              <Icon name="arrow" />
            </button>
          </form>
          <div className="login-foot">
            <Icon name="shield" size={16} /> POC role selection · Access is enforced by the backend
          </div>
        </section>
      </main>
      <footer className="login-footer">
        Retail Inventory Agent <span>Oracle Redwood–inspired experience</span>
      </footer>
    </div>
  );
}

export default function App() {
  const [session, setSession] = useState(null),
    [checking, setChecking] = useState(true),
    [expired, setExpired] = useState(false),
    [restoreError, setRestoreError] = useState(null);
  async function restore() {
    setChecking(true);
    setRestoreError(null);
    const saved = readSession();
    if (saved?.session_token) {
      setToken(saved.session_token);
      try {
        const verified = await api.me();
        setSession({ ...saved, ...verified });
      } catch (e) {
        if (e.status !== 401) setRestoreError(e);
        else sessionStorage.removeItem(SESSION);
        setToken(null);
      }
    }
    setChecking(false);
  }
  useEffect(() => {
    restore();
    const expire = () => {
      sessionStorage.removeItem(SESSION);
      setToken(null);
      setSession(null);
      setExpired(true);
    };
    window.addEventListener('session-expired', expire);
    return () => window.removeEventListener('session-expired', expire);
  }, []);
  function login(value) {
    sessionStorage.setItem(SESSION, JSON.stringify(value));
    setToken(value.session_token);
    setSession(value);
    setExpired(false);
  }
  async function logout() {
    await api.logout();
    sessionStorage.removeItem(SESSION);
    setToken(null);
    setSession(null);
  }
  if (checking) return <Loading label="Restoring your workspace…" />;
  if (restoreError)
    return (
      <div className="restore-error">
        <h1>Workspace connection interrupted</h1>
        <ErrorNotice error={restoreError} retry={restore} />
      </div>
    );
  return session ? (
    <Workspace key={session.session_id} session={session} onLogout={logout} />
  ) : (
    <Login onLogin={login} expired={expired} />
  );
}

function Workspace({ session, onLogout }) {
  const [page, setPage] = useState('overview'),
    [menu, setMenu] = useState(false),
    [item, setItem] = useState(null),
    [chatTarget, setChatTarget] = useState(null),
    [thread, setThread] = useState(() =>
      sessionStorage.getItem(`retail.thread.${session.session_id}`),
    ),
    [revision, setRevision] = useState(0);
  const logout = useAction();
  const health = useResource(api.health, []);
  const retail = session.role === 'retail-manager';
  const navigation = modules.filter((m) => m.roles.includes(session.role));
  function navigate(next, selectedItem) {
    const destination = ['cases', 'supplier-agent'].includes(next) ? 'activity'
      : ['workflows', 'inventory', 'supplier', 'suppliers', 'policy'].includes(next) ? 'assistant' : next;
    setPage(destination);
    setMenu(false);
    if (selectedItem) setItem(selectedItem);
    if (destination === 'assistant' && selectedItem) {
      setChatTarget({ ...selectedItem, navigationId: crypto.randomUUID() });
    }
  }
  function saveThread(id) {
    setThread(id);
    if (id) sessionStorage.setItem(`retail.thread.${session.session_id}`, id);
    else sessionStorage.removeItem(`retail.thread.${session.session_id}`);
  }
  const shared = {
    session,
    navigate,
    item,
    thread,
    setThread: saveThread,
    revision,
    onChange: () => setRevision((x) => x + 1),
    chatTarget,
  };
  const titles = {
    overview: retail ? 'Inventory overview' : 'Supplier overview',
    inventory: 'Inventory intelligence',
    workflows: 'Replenishment workspace',
    'supplier-agent': 'Supplier agent workspace',
    cases: 'Cases & approvals',
    suppliers: 'Supplier comparison',
    supplier: 'Inventory & quotes',
    policy: 'Supplier policy',
    assistant: retail ? 'Retail Agent Chat' : 'Supplier Agent Chat',
    activity: 'Request Activity',
    'agent-registry': 'Agent registry',
    platform: 'Platform & roadmap',
  };
  const subtitles = {
    overview: 'A clear view of your inventory. A confident next step.',
    inventory: 'Spot stock risks and turn demand signals into replenishment decisions.',
    workflows: 'From inventory assessment to an approved supplier request.',
    'supplier-agent':
      'Open an assigned case to check terms, preview the response, and approve the agent’s next step.',
    cases: 'Review the facts, record a decision, and track what happens next.',
    suppliers: 'Compare policy-backed quotes, availability, and delivery terms.',
    supplier: 'Check your available stock and prepare a policy-backed quote.',
    policy: 'Your supplier policy, retrieved and verified by the backend.',
    assistant: 'Work with your agent, review its recommendations, and approve each decision here.',
    activity: 'Follow requests, supplier responses, and delivery outcomes in one place.',
    'agent-registry': 'Register hosted agents and manage their availability.',
    platform: 'Connected capabilities today. A foundation for what comes next.',
  };
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <header className="topbar">
        <button
          className="icon-button mobile-menu"
          aria-label="Toggle navigation"
          aria-expanded={menu}
          onClick={() => setMenu(!menu)}
        >
          <Icon name="menu" />
        </button>
        <Brand />
        <div className="topbar-right">
          <span className="environment">POC ENVIRONMENT</span>
          <span className="top-divider" />
          <div className="user-avatar">{retail ? 'RM' : 'SP'}</div>
          <div className="user-label">
            <strong>{retail ? 'Retail manager' : session.supplier_id}</strong>
            <span>{retail ? 'Operations workspace' : 'Supplier workspace'}</span>
          </div>
        </div>
      </header>
      <aside className={`sidebar ${menu ? 'open' : ''}`}>
        <div className="workspace-label">WORKSPACE</div>
        <nav aria-label="Main navigation">
          {navigation.map((m) => (
            <button
              key={m.id}
              aria-current={page === m.id ? 'page' : undefined}
              className={page === m.id ? 'nav-item active' : 'nav-item'}
              onClick={() => navigate(m.id)}
            >
              <Icon name={m.icon} />
              <span>{m.label}</span>
              {page === m.id && <span className="nav-indicator" />}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="connection">
            <span className={`dot ${health.data ? 'online' : ''}`} />
            {health.loading ? 'Checking API…' : health.data ? 'API available' : 'API unavailable'}
            <button
              className="icon-button"
              aria-label="Recheck API connection"
              onClick={health.reload}
            >
              <Icon name="refresh" size={14} />
            </button>
          </div>
          <button
            className="nav-item"
            disabled={logout.busy}
            onClick={() => logout.run('logout', onLogout)}
          >
            <Icon name="logout" />
            <span>Sign out</span>
          </button>
        </div>
      </aside>
      <main id="main" className="main-content">
        <div className="breadcrumb">
          Retail operations <Icon name="chevron" size={13} />
          <span>{titles[page]}</span>
        </div>
        <div className="page-heading">
          <div>
            <div className="eyebrow">
              {retail ? 'RETAIL MANAGER' : `SUPPLIER · ${session.supplier_id}`}
            </div>
            <h1>{titles[page]}</h1>
            <p>{subtitles[page]}</p>
          </div>
        </div>
        <ErrorNotice error={logout.error} />
        {page === 'overview' && <Overview {...shared} />}
        {page === 'activity' && <RequestActivity {...shared} />}
        <div hidden={page !== 'assistant'}>
          <AgentChat {...shared} active={page === 'assistant'} />
        </div>
        <footer className="workspace-footer">
          <span>Retail Inventory Agent</span>
          <span>Human-led decisions. Agent-assisted operations.</span>
        </footer>
      </main>
    </div>
  );
}
