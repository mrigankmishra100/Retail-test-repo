import { useEffect, useState } from 'react';
import api from '../api';
import {
  Badge,
  Dialog,
  Empty,
  ErrorNotice,
  Icon,
  Loading,
  Panel,
  Status,
  human,
  money,
  number,
  useAction,
  useResource,
} from '../ui';
import { Pagination, QuoteCard, QuoteComparison } from './Workspace';
import SupplierWorkflow from './SupplierWorkflow';
import { EmailSnapshot, CaseProgress, ApprovedSummary } from './CaseProgress';

const deliveryIssue = (value) => ['FAILED', 'UNKNOWN'].includes(value);

function NotificationStatus({ record }) {
  const states = [
    ['Request', record.request_notification_status],
    ['Response', record.response_notification_status],
  ];
  return (
    <div className="case-notifications">
      {states.map(([label, status]) => (
        <div key={label}>
          <small>{label}</small>
          {status ? <Status value={status} /> : <Badge>Not yet sent</Badge>}
        </div>
      ))}
    </div>
  );
}

function workflowAssessment(workflow) {
  const itemId = workflow?.comparison?.item_id || workflow?.case?.item_id;
  if (!itemId || !workflow?.inventory) return null;
  return ['at_risk_items', 'not_at_risk_items', 'unassessed_items']
    .flatMap((key) => workflow.inventory[key] || [])
    .find((item) => item.item_id === itemId);
}

function plainAgentText(value) {
  return String(value || '')
    .replace(/#{1,6}\s*/g, '')
    .replace(/[\*_`]/g, '')
    .replace(/\s+/g, ' ')
    .trim();
}

function AgentInsight({ workflow }) {
  const comparison = workflow.comparison;
  const assessment = workflowAssessment(workflow);
  const recommended = comparison?.suppliers.find(
    (supplier) => supplier.supplier_id === comparison.recommended_supplier_id,
  );
  const caution =
    assessment?.history_quality?.warnings?.[0] || recommended?.history_quality?.warnings?.[0];
  return (
    <section className="agent-insight" aria-labelledby="agent-insight-title">
      <header className="agent-insight-header">
        <span className="agent-insight-icon">
          <Icon name="spark" size={20} />
        </span>
        <div>
          <strong id="agent-insight-title">Agent insight</strong>
          <small>Decision support from verified workflow facts</small>
        </div>
        <Badge tone="success">AI advisory</Badge>
      </header>
      <p className="agent-insight-summary">{plainAgentText(workflow.advisory)}</p>
      <div className="agent-insight-metrics">
        <div>
          <span>Projected stock</span>
          <strong>
            {assessment?.projected_stock == null
              ? 'Not assessed'
              : `${number(assessment.projected_stock)} units`}
          </strong>
        </div>
        <div>
          <span>Quantity under review</span>
          <strong>{comparison ? `${number(comparison.quantity)} units` : 'Not available'}</strong>
        </div>
        <div>
          <span>Leading option</span>
          <strong>{recommended?.supplier_name || recommended?.supplier_id || 'None feasible'}</strong>
        </div>
      </div>
      <div className="agent-insight-reason">
        <Icon name={recommended ? 'check' : 'alert'} size={17} />
        <div>
          <strong>{recommended ? 'Why this option leads' : 'Supplier review required'}</strong>
          <span>
            {recommended
              ? `${recommended.supplier_name || recommended.supplier_id} is the leading feasible option at ${money(recommended.priced_total)} with ${number(recommended.lead_time_days)}-day lead time.`
              : 'The comparison did not identify a feasible supplier for the requested quantity.'}
          </span>
        </div>
      </div>
      {caution && (
        <div className="agent-insight-warning">
          <Icon name="alert" size={16} />
          <span>{caution}</span>
        </div>
      )}
      <footer>
        <Icon name="shield" size={14} /> Advisory only — this insight cannot select a supplier or
        record an approval.
      </footer>
    </section>
  );
}

function PreviousSupplierHistory({ itemId, loading, error, records, retry }) {
  const record = records?.[0];
  const terms = record?.approved_terms;
  const quote = terms?.quote;
  if (loading) {
    return (
      <section className="previous-supplier previous-supplier-state" aria-live="polite">
        <span className="previous-supplier-icon"><Icon name="people" size={19} /></span>
        <div>
          <strong>Previous supplier</strong>
          <small>Checking completed replenishment history…</small>
        </div>
      </section>
    );
  }
  if (error) {
    return (
      <section className="previous-supplier previous-supplier-state">
        <span className="previous-supplier-icon"><Icon name="alert" size={19} /></span>
        <div>
          <strong>Previous supplier history unavailable</strong>
          <small>The current supplier comparison is unaffected.</small>
        </div>
        <button onClick={retry}>Retry</button>
      </section>
    );
  }
  if (!terms || !quote) {
    return (
      <section className="previous-supplier previous-supplier-state">
        <span className="previous-supplier-icon"><Icon name="people" size={19} /></span>
        <div>
          <strong>No previous supplier</strong>
          <small>No completed replenishment history is recorded for {itemId}.</small>
        </div>
      </section>
    );
  }
  return (
    <section className="previous-supplier" aria-labelledby="previous-supplier-title">
      <span className="previous-supplier-icon"><Icon name="people" size={19} /></span>
      <div className="previous-supplier-name">
        <span id="previous-supplier-title">Previous supplier</span>
        <strong>{quote.supplier_name || terms.supplier_id}</strong>
        <small>{terms.supplier_id} · Last completed replenishment</small>
      </div>
      <div className="previous-supplier-details">
        <span>{number(terms.quantity)} {quote.unit || 'units'}</span>
        <span>{money(quote.priced_total)}</span>
        <span>{number(quote.lead_time_days)}-day lead time</span>
        <span>Case #{String(record.case_id).slice(0, 8)}</span>
      </div>
    </section>
  );
}

export function Cases({ session, revision, onChange }) {
  const [offset, setOffset] = useState(0),
    [selected, setSelected] = useState(null),
    [filter, setFilter] = useState('all'),
    [search, setSearch] = useState(''),
    [sort, setSort] = useState('newest');
  const resource = useResource(() => api.cases(offset), [offset, revision], 15000);
  const pendingStatus =
    session.role === 'supplier' ? 'AWAITING_SUPPLIER_APPROVAL' : 'AWAITING_MANAGER_APPROVAL';
  const pageItems = resource.data?.items || [];
  const query = search.trim().replace(/^#/, '').toLowerCase();
  const counts = {
    manager: pageItems.filter((c) => c.status === 'AWAITING_MANAGER_APPROVAL').length,
    supplier: pageItems.filter((c) => c.status === 'AWAITING_SUPPLIER_APPROVAL').length,
    completed: pageItems.filter((c) => c.status === 'COMPLETED').length,
    issues: pageItems.filter(
      (c) =>
        c.status.endsWith('REJECTED') ||
        deliveryIssue(c.request_notification_status) ||
        deliveryIssue(c.response_notification_status),
    ).length,
  };
  const items = pageItems
    .filter(
      (c) =>
        filter === 'all' ||
        (filter === 'pending' ? c.status === pendingStatus : c.status === 'COMPLETED'),
    )
    .filter(
      (c) =>
        !query ||
        c.case_id.toLowerCase().includes(query) ||
        c.item_id.toLowerCase().includes(query) ||
        c.selected_supplier_id?.toLowerCase().includes(query),
    );
  if (sort === 'status') items.sort((a, b) => human(a.status).localeCompare(human(b.status)));
  if (sort === 'supplier')
    items.sort((a, b) =>
      (a.selected_supplier_id || 'ZZZ').localeCompare(b.selected_supplier_id || 'ZZZ'),
    );
  if (sort === 'quantity')
    items.sort((a, b) => b.recommended_quantity - a.recommended_quantity);
  return (
    <>
      <Panel
        title="Replenishment cases"
        subtitle="Shared case progress · refreshes every 15 seconds while visible"
        action={
          <button disabled={resource.loading} onClick={resource.reload}>
            <Icon name="refresh" size={16} /> Refresh
          </button>
        }
      >
        <div className="case-kpis" aria-label="Case status summary for the current page">
          {[
            ['Awaiting manager', counts.manager, 'warning'],
            ['Awaiting supplier', counts.supplier, 'warning'],
            ['Completed', counts.completed, 'success'],
            ['Failed or rejected', counts.issues, 'danger'],
          ].map(([label, value, tone]) => (
            <section className={`case-kpi ${tone}`} key={label}>
              <span>{label}</span>
              <strong>{value}</strong>
              <small>Current page</small>
            </section>
          ))}
        </div>
        <div className="tabs">
          {[
            ['all', 'All cases'],
            ['pending', 'Awaiting my approval'],
            ['completed', 'Completed'],
          ].map(([id, label]) => (
            <button
              key={id}
              aria-pressed={filter === id}
              className={filter === id ? 'active' : ''}
              onClick={() => setFilter(id)}
            >
              {label}
            </button>
          ))}
        </div>
        <div className="case-list-tools">
          <div className="search-field">
            <Icon name="search" size={16} />
            <input
              type="search"
              aria-label="Search cases"
              placeholder="Search case, item, or supplier"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </div>
          <label className="inline-label">
            Sort by
            <select
              aria-label="Sort cases"
              value={sort}
              onChange={(event) => setSort(event.target.value)}
            >
              <option value="newest">Newest first</option>
              <option value="status">Status</option>
              <option value="supplier">Supplier</option>
              <option value="quantity">Quantity: high to low</option>
            </select>
          </label>
          <span className="case-result-count">
            {items.length} of {pageItems.length} on this page
          </span>
        </div>
        {(filter !== 'all' || search) && resource.data?.has_more && (
          <div className="table-footer">Search and filters apply to this page. Use Next for more records.</div>
        )}
        <ErrorNotice error={resource.error} retry={resource.reload} />
        {resource.loading ? (
          <Loading />
        ) : (
          !resource.error &&
          (items.length ? (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Case</th>
                    <th>Item</th>
                    <th>Quantity</th>
                    <th>Supplier</th>
                    <th>Status</th>
                    <th>Notification status</th>
                    <th>Action</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((c) => (
                    <tr key={c.case_id}>
                      <td>
                        <strong>#{c.case_id.slice(0, 8)}</strong>
                        <small>Version {c.version}</small>
                      </td>
                      <td>{c.item_id}</td>
                      <td>{number(c.recommended_quantity)} units</td>
                      <td>{c.selected_supplier_id || 'Not selected'}</td>
                      <td>
                        <Status value={c.status} />
                      </td>
                      <td>
                        <NotificationStatus record={c} />
                      </td>
                      <td>
                        <button className="text-button" onClick={() => setSelected(c.case_id)}>
                          Review <Icon name="chevron" size={14} />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <Empty title={search ? 'No matching cases' : 'No cases in this view'} icon="file">
              {search
                ? 'Try a different case ID, item ID, or supplier ID.'
                : 'New replenishment cases will appear here. Try another filter or page.'}
            </Empty>
          ))
        )}
        <Pagination
          offset={offset}
          hasMore={resource.data?.has_more}
          onChange={setOffset}
          disabled={resource.loading}
        />
      </Panel>
      {selected && (
        <Dialog title="Case review" onClose={() => setSelected(null)}>
          <CaseDetail id={selected} session={session} onChange={onChange} />
        </Dialog>
      )}
    </>
  );
}

export function CaseDetail({ id, session, onChange }) {
  const resource = useResource(() => api.case(id), [id], 10000);
  const action = useAction();
  const [delivery, setDelivery] = useState(null);
  async function changed() {
    resource.reload();
    onChange?.();
  }
  async function dispatch(kind) {
    const result = await action.run('dispatch', () => api.dispatch(id, kind));
    if (result) {
      setDelivery(result.delivery_status || '');
      changed();
    }
  }
  async function complete() {
    const result = await action.run(`complete:${id}`, (key) => api.complete(id, key));
    if (result) changed();
  }
  const c = resource.data,
    retail = session.role === 'retail-manager';
  return (
    <>
      <ErrorNotice error={resource.error} retry={resource.reload} />
      {resource.loading ? (
        <Loading />
      ) : (
        c && (
          <>
            <div className="detail-title">
              <Status value={c.status} />
              <button onClick={resource.reload}>
                <Icon name="refresh" size={15} /> Refresh
              </button>
            </div>
            <p className="hash">
              Request {c.case_id} · Version {c.version}
            </p>
            <dl className="facts">
              <div>
                <dt>Item</dt>
                <dd>{c.item_id}</dd>
              </div>
              <div>
                <dt>Current stock</dt>
                <dd>{number(c.current_stock)}</dd>
              </div>
              <div>
                <dt>Requested quantity</dt>
                <dd>{number(c.recommended_quantity)}</dd>
              </div>
              <div>
                <dt>Selected supplier</dt>
                <dd>{c.selected_supplier_id || 'Not selected'}</dd>
              </div>
            </dl>
            <details className="request-progress-details"><summary>View request progress and recorded decisions</summary><CaseProgress record={c} /></details>
            {c.draft?.quote && <QuoteCard quote={c.draft.quote} />}
            {retail && c.draft && c.draft_hash ? (
              <ManagerEmailReview
                key={`${c.case_id}:${c.version}`}
                record={c}
                session={session}
                onChange={changed}
                disabled={Boolean(resource.error)}
              />
            ) : (
              !retail && (
                <SupplierWorkflow
                  record={c}
                  session={session}
                  onChange={changed}
                  Decision={DecisionPanel}
                  disabled={Boolean(resource.error)}
                />
              )
            )}
            {retail && ['RESPONSE_QUEUED', 'SUPPLIER_RESPONDED', 'SUPPLIER_REJECTED', 'COMPLETED'].includes(c.status) && (
              <EmailSnapshot
                email={c.draft?.supplier_response}
                title="Supplier response preview"
                approved={['RESPONSE_QUEUED', 'SUPPLIER_RESPONDED', 'COMPLETED'].includes(c.status)}
                rejected={c.status === 'SUPPLIER_REJECTED'}
              />
            )}
            {retail && !resource.error && c.status === 'DRAFT' && <PrepareDraft record={c} onChange={changed} />}
            <ErrorNotice error={action.error} />
            {delivery && (
              <div role="status" className="callout">
                Delivery status: {human(delivery)}. Refresh for the latest result.
              </div>
            )}
            {c.notifications?.length > 0 && (
              <div className="notification-list">
                <h3>Delivery tracking</h3>
                {c.notifications.map((n) => (
                  <div key={n.type}>
                    <span>{human(n.type)}</span>
                    <Status value={n.status} />
                  </div>
                ))}
              </div>
            )}
            {retail && c.status === 'REQUEST_QUEUED' && (
              <div className="action-box">
                <p>
                  The decision is recorded. Dispatch publishes the approved{' '}
                  {retail ? 'supplier request' : 'supplier response'} through the configured
                  notification service.
                </p>
                <button
                  className="primary"
                  disabled={
                    action.busy ||
                    Boolean(resource.error) ||
                    c.notifications?.some(
                      (n) =>
                        n.type === 'SUPPLIER_REQUEST' &&
                        ['FAILED', 'UNKNOWN', 'SENDING'].includes(n.status),
                    )
                  }
                  onClick={() => dispatch(retail ? 'request' : 'response')}
                >
                  {action.busy
                    ? 'Dispatching…'
                    : retail
                      ? 'Dispatch approved email'
                      : 'Dispatch response'}
                </button>
              </div>
            )}
            {retail && c.status === 'SUPPLIER_RESPONDED' && (
              <div className="action-box">
                <p>
                  The supplier has responded. Complete this request to save the approved summary.
                </p>
                <button className="primary" disabled={action.busy || Boolean(resource.error)} onClick={complete}>
                  Complete request
                </button>
              </div>
            )}
            {retail && <ApprovedSummary record={c} />}
          </>
        )
      )}
    </>
  );
}
export function PrepareDraft({ record, onChange }) {
  const quotes = useResource(
    () => api.compare(record.item_id, record.recommended_quantity),
    [record.case_id],
  );
  const action = useAction();
  async function prepare(q) {
    const result = await action.run(
      `prepare:${record.case_id}:${record.version}:${q.supplier_id}`,
      (key) =>
        api.prepare(record.case_id, { supplier_id: q.supplier_id, version: record.version }, key),
    );
    if (result) onChange();
  }
  return (
    <>
      <h3>Choose a supplier to prepare this draft</h3>
      <ErrorNotice error={quotes.error} retry={quotes.reload} />
      <ErrorNotice error={action.error} />
      {quotes.loading ? (
        <Loading />
      ) : (
        quotes.data && (
          <QuoteComparison
            quotes={quotes.data.suppliers}
            recommendedSupplierId={quotes.data.recommended_supplier_id}
            onSelect={prepare}
            busy={action.busy}
          />
        )
      )}
    </>
  );
}

export function ManagerEmailReview({ record, session, onChange, onRefresh, disabled = false, compact = false }) {
  const preview = useResource(
    () => api.supplierEmail(record.case_id),
    [record.case_id, record.version, record.draft_hash],
  );
  const verified = Boolean(
    !preview.loading &&
    !preview.error &&
    preview.data &&
    preview.data.case_id === record.case_id &&
    preview.data.version === record.version &&
    preview.data.draft_hash === record.draft_hash,
  );
  const approved = !['DRAFT', 'AWAITING_MANAGER_APPROVAL', 'MANAGER_REJECTED'].includes(
    record.status,
  );

  return (
    <>
      <section className="email-review-region" aria-label="Supplier email preview">
        <div className="email-snapshot-heading">
          <span className="email-icon" aria-hidden="true">
            <Icon name="file" size={18} />
          </span>
          <div>
            <h3>{approved ? 'Approved supplier email' : 'Review the supplier email'}</h3>
            <p>
              {approved
                ? 'This is the exact email snapshot approved by the manager.'
                : 'This exact email snapshot will be bound to your approval.'}
            </p>
          </div>
          <Badge
            tone={
              record.status === 'MANAGER_REJECTED' ? 'danger' : approved ? 'success' : 'warning'
            }
          >
            {record.status === 'MANAGER_REJECTED'
              ? 'Manager rejected'
              : approved
                ? 'Manager approved'
                : 'Approval pending'}
          </Badge>
        </div>
        {preview.loading && <Loading label="Loading verified email preview…" />}
        <ErrorNotice error={preview.error} retry={preview.reload} />
        {!preview.loading && preview.data && !verified && (
          <div className="callout" role="alert">
            The email snapshot no longer matches this case version. Refresh before approving.
          </div>
        )}
        {verified && (
          <EmailSnapshot
            email={preview.data.email}
            title="Retail request email"
            approved={approved}
            rejected={record.status === 'MANAGER_REJECTED'}
          />
        )}
      </section>
      <DecisionPanel
        record={record}
        session={session}
        onChange={onChange}
        onRefresh={onRefresh}
        reviewReady={verified && !disabled}
        compact={compact}
      />
    </>
  );
}

export function DecisionPanel({ record, session, onChange, onRefresh, reviewReady = true, compact = false }) {
  const action = useAction();
  const [comment, setComment] = useState(''),
    [confirmed, setConfirmed] = useState(false);
  const allowed =
    record.status ===
    (session.role === 'supplier' ? 'AWAITING_SUPPLIER_APPROVAL' : 'AWAITING_MANAGER_APPROVAL');
  if (!allowed || !record.draft_hash || !record.draft) return null;
  const conflict = action.error?.status === 409;
  async function decide(approved) {
    const body = {
      approved,
      version: record.version,
      draft_hash: record.draft_hash,
      comment: comment || null,
    };
    const result = await action.run(`decision:${record.case_id}:${JSON.stringify(body)}`, (key) =>
      api.decision(record.case_id, session.role, body, key),
    );
    if (result) onChange(result);
  }
  const commentTooLong = new TextEncoder().encode(comment).length > 2000;
  return (
    <section className="decision-box">
      <div className="section-icon-title">
        <Icon name="shield" />
        <h3>{compact ? 'Does this draft look right?' : 'Your decision is required'}</h3>
      </div>
      <p>
        Review the displayed supplier, quantity, price, exclusions, policy terms, and supplier email
        when provided. Your decision applies to version {record.version} of this exact draft.
      </p>
      <details open={compact ? undefined : true} className="decision-comment">
      <summary>Add a decision comment (optional)</summary>
      <label>
        Decision comment <span className="muted">(optional)</span>
        <textarea
          rows="3"
          maxLength="2000"
          value={comment}
          disabled={action.busy}
          onChange={(e) => setComment(e.target.value)}
          placeholder="Add context for your decision…"
        />
      </label>
      </details>
      {commentTooLong && <p role="alert">Comment must be at most 2,000 UTF-8 bytes.</p>}
      <label className="checkbox-label">
        <input
          type="checkbox"
          checked={confirmed}
          disabled={action.busy || conflict}
          onChange={(e) => setConfirmed(e.target.checked)}
        />{' '}
        {session.role === 'retail-manager'
          ? 'I have reviewed this draft, its supplier email, and its commercial terms.'
          : 'I have reviewed the supplier response, its audience, and its commercial terms.'}
      </label>
      {!reviewReady && (
        <p role="status">
          Approval unlocks after the saved preview and required checks are ready. Rejection remains
          available.
        </p>
      )}
      <ErrorNotice error={action.error} />
      {conflict && (
        <button
          onClick={() => {
            action.clear();
            setConfirmed(false);
            if (onRefresh) onRefresh();
            else onChange(record);
          }}
        >
          Refresh case for review
        </button>
      )}
      <div className="button-row">
        <button
          className="primary"
          disabled={!confirmed || action.busy || commentTooLong || !reviewReady || conflict}
          onClick={() => decide(true)}
        >
          <Icon name="check" size={16} />
          {action.busy ? 'Recording…' : 'Approve draft'}
        </button>
        <button
          className="danger-button"
          disabled={!confirmed || action.busy || commentTooLong || conflict}
          onClick={() => decide(false)}
        >
          Reject draft
        </button>
      </div>
      <small>Approval is recorded separately from notification dispatch.</small>
    </section>
  );
}

export function Workflow({ session, item, thread, setThread, onChange, navigate }) {
  const [id, setId] = useState(item?.item_id || ''),
    [quantity, setQuantity] = useState(item?.recommended_quantity || ''),
    [workflow, setWorkflow] = useState(null),
    [restoring, setRestoring] = useState(false);
  const [syncError, setSyncError] = useState(null);
  const action = useAction();
  const historyItemId = workflow?.comparison?.item_id || workflow?.case?.item_id || null;
  const history = useResource(
    async () => ({
      itemId: historyItemId,
      page: historyItemId
        ? await api.approvedMemory(historyItemId)
        : { items: [], limit: 5, offset: 0, has_more: false },
    }),
    [historyItemId],
  );
  const historyMatches = history.data?.itemId === historyItemId;
  const historyRecords = historyMatches ? history.data.page.items : [];
  const previousSupplierId = historyRecords[0]?.approved_terms?.supplier_id || null;
  async function refresh() {
    if (!thread) return;
    setRestoring(true);
    const result = await action.run('refresh', () => api.workflow(thread));
    if (result) {
      setWorkflow(result);
      setSyncError(null);
    }
    setRestoring(false);
  }
  useEffect(() => {
    if (thread) refresh();
  }, [thread]);
  useEffect(() => {
    if (!thread || action.busy) return;
    let active = true,
      timer;
    async function sync() {
      try {
        if (document.visibilityState !== 'hidden') {
          const latest = await api.workflow(thread);
          if (active) {
            setWorkflow((w) => (w?.case?.version > latest.case?.version ? w : latest));
            setSyncError(null);
          }
        }
      } catch (error) {
        if (active) setSyncError(error);
      } finally {
        if (active) timer = setTimeout(sync, 10000);
      }
    }
    timer = setTimeout(sync, 10000);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [thread, action.busy]);
  function update(result) {
    setWorkflow(result);
    setThread(result.thread_id);
    onChange();
  }
  async function start(e) {
    e.preventDefault();
    const body = {
      ...(id ? { item_id: id } : {}),
      ...(quantity ? { quantity: Number(quantity) } : {}),
    };
    const result = await action.run(`start:${JSON.stringify(body)}`, (key) => api.start(body, key));
    if (result) update(result);
  }
  async function select(q) {
    const result = await action.run(`select:${q.supplier_id}`, () =>
      api.select(thread, { supplier_id: q.supplier_id, quantity: q.quantity }),
    );
    if (result) update(result);
  }
  async function resume() {
    const result = await action.run('resume', () => api.resume(thread));
    if (result) update(result);
  }
  const status = workflow?.status;
  const step = workflow?.case ? 3 : workflow?.comparison ? 2 : workflow ? 1 : 0;
  return (
    <>
      <div className="workflow-steps">
        {['Assess inventory', 'Compare suppliers', 'Review draft', 'Approve & dispatch'].map(
          (label, i) => (
            <div className={i < step ? 'done' : i === step ? 'current' : ''} key={label}>
              <span>{i < step ? <Icon name="check" size={15} /> : i + 1}</span>
              <strong>{label}</strong>
              {i < 3 && <Icon name="chevron" size={16} />}
            </div>
          ),
        )}
      </div>
      <ErrorNotice error={action.error} />
      <ErrorNotice error={syncError} retry={refresh} />
      {!thread ? (
        <Panel
          title="Start a replenishment assessment"
          subtitle="The retail agent checks stock, demand, and supplier feasibility"
        >
          <form className="form-toolbar" onSubmit={start}>
            <label>
              Item ID <span className="muted">(optional)</span>
              <input
                placeholder="Auto-select an at-risk item"
                pattern="ITEM[0-9]{3}"
                value={id}
                onChange={(e) => setId(e.target.value.toUpperCase())}
                disabled={action.busy}
              />
            </label>
            <label>
              Quantity <span className="muted">(optional)</span>
              <input
                type="number"
                placeholder="Use recommendation"
                min="1"
                max="1000000"
                value={quantity}
                onChange={(e) => setQuantity(e.target.value)}
                disabled={action.busy}
              />
            </label>
            <button className="primary" disabled={action.busy}>
              <Icon name="spark" size={17} />
              {action.busy ? 'Assessing inventory…' : 'Run assessment'}
            </button>
          </form>
          <div className="table-footer">
            Leave fields blank to let the backend select an at-risk item and calculate the quantity.
          </div>
        </Panel>
      ) : (
        <Panel
          title="Active retail workflow"
          subtitle={`Thread ${thread}`}
          action={
            <div className="button-row">
              <button disabled={action.busy} onClick={refresh}>
                <Icon name="refresh" size={16} /> Refresh
              </button>
              <button
                disabled={action.busy}
                onClick={() => {
                  setThread(null);
                  setWorkflow(null);
                  action.clear();
                }}
              >
                New assessment
              </button>
            </div>
          }
        >
          {restoring ? (
            <Loading label="Restoring workflow checkpoint…" />
          ) : workflow ? (
            <div className="workflow-summary">
              <Status value={status} />
              <p>{workflow.message}</p>
              {workflow.error_code && <small>Backend status: {workflow.error_code}</small>}
              {workflow.advisory && <AgentInsight workflow={workflow} />}
              {workflow.can_resume &&
                workflow.pending_input !== 'selection' &&
                !(
                  workflow.pending_input === 'manager_approval' &&
                  workflow.case?.status === 'AWAITING_MANAGER_APPROVAL'
                ) && (
                  <div className="action-box">
                    <p>
                      Continue from the saved checkpoint. If approval is recorded, this step may
                      dispatch the approved supplier request.
                    </p>
                    <button
                      className="primary"
                      disabled={
                        action.busy ||
                        Boolean(syncError) ||
                        status === 'delivery_reconciliation_required'
                      }
                      onClick={resume}
                    >
                      {action.busy ? 'Continuing…' : 'Resume workflow'}
                    </button>
                    {status === 'delivery_reconciliation_required' && (
                      <p>Delivery needs backend reconciliation before continuing.</p>
                    )}
                  </div>
                )}
            </div>
          ) : (
            !action.error && <Loading />
          )}
        </Panel>
      )}
      {workflow?.comparison && (
        <>
          <div className="section-heading">
            <div>
              <h2>Supplier recommendations</h2>
              <p>{workflow.comparison.rationale}</p>
            </div>
            <Badge>
              {workflow.comparison.item_id} · {number(workflow.comparison.quantity)} requested
            </Badge>
          </div>
          <PreviousSupplierHistory
            itemId={historyItemId}
            loading={Boolean(historyItemId) && (history.loading || !historyMatches) && !history.error}
            error={history.error}
            records={historyRecords}
            retry={history.reload}
          />
          <QuoteComparison
            quotes={workflow.comparison.suppliers}
            recommendedSupplierId={workflow.comparison.recommended_supplier_id}
            previousSupplierId={previousSupplierId}
            onSelect={workflow.pending_input === 'selection' ? select : undefined}
            busy={action.busy}
          />
        </>
      )}
      {workflow?.case && (
        <Panel
          title="Replenishment draft"
          subtitle={`Case ${workflow.case.case_id}`}
          action={<Status value={workflow.case.status} />}
        >
          <div className="padded">
            <dl className="facts">
              <div>
                <dt>Item</dt>
                <dd>{workflow.case.item_id}</dd>
              </div>
              <div>
                <dt>Quantity</dt>
                <dd>{number(workflow.case.recommended_quantity)} units</dd>
              </div>
            </dl>
            <CaseProgress record={workflow.case} />
            {workflow.case.draft?.quote && <QuoteCard quote={workflow.case.draft.quote} />}
            {session.role === 'retail-manager' &&
            workflow.case.draft &&
            workflow.case.draft_hash ? (
              <ManagerEmailReview
                key={`${workflow.case.case_id}:${workflow.case.version}`}
                record={workflow.case}
                session={session}
                disabled={Boolean(syncError)}
                onRefresh={refresh}
                onChange={(record) => {
                  setWorkflow((w) => ({ ...w, case: record }));
                  onChange();
                }}
              />
            ) : (
              <DecisionPanel
                key={`${workflow.case.case_id}:${workflow.case.version}`}
                record={workflow.case}
                session={session}
                onChange={(record) => {
                  setWorkflow((w) => ({ ...w, case: record }));
                  onChange();
                }}
              />
            )}
            <EmailSnapshot
              email={workflow.case.draft?.supplier_response}
              title="Supplier response preview"
              approved={['RESPONSE_QUEUED', 'SUPPLIER_RESPONDED', 'COMPLETED'].includes(
                workflow.case.status,
              )}
              rejected={workflow.case.status === 'SUPPLIER_REJECTED'}
            />
            <ApprovedSummary record={workflow.case} />
            <button className="text-button" onClick={() => navigate('cases')}>
              Open case tracking <Icon name="arrow" size={16} />
            </button>
          </div>
        </Panel>
      )}
      {!workflow && !thread && !action.busy && (
        <Empty title="From stock risk to a ready-to-review draft" icon="flow">
          The agent pauses for supplier selection and your explicit approval.
        </Empty>
      )}
    </>
  );
}
