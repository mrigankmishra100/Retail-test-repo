import { useEffect, useRef, useState } from 'react';
import api from '../api';
import { ErrorNotice, Icon, Loading, Status, human, money, number, useAction, useResource } from '../ui';
import { QuoteCard } from './Workspace';
import { DecisionPanel, ManagerEmailReview, PrepareDraft } from './Operations';
import { ApprovedSummary, CaseProgress, EmailSnapshot } from './CaseProgress';
import SupplierWorkflow from './SupplierWorkflow';

export function WorkflowReply({ label, step, children }) {
  return <section className="workflow-reply" aria-label={label}>
    <span className="message-avatar"><Icon name="bot" size={19} /></span>
    <div className="chat-workflow-card">
      <div className="workflow-reply-label">{step}</div>
      {children}
    </div>
  </section>;
}

export function ChatQuotes({ quotes, recommendedSupplierId, onSelect, busy }) {
  return <div className="chat-supplier-options">
    {quotes.map((q) => <article className="chat-supplier-option" key={q.supplier_id}>
      <div><strong>{q.supplier_name || q.supplier_id}</strong>{q.supplier_id === recommendedSupplierId && <small>Recommended</small>}</div>
      <p>{q.priced_total == null ? 'No confirmed price' : `${money(q.priced_total)} for ${number(q.quantity)} ${q.unit || 'units'}`} · {q.lead_time_days == null ? 'Delivery unconfirmed' : `${q.lead_time_days} day delivery`}</p>
      <p>{q.feasible ? 'Feasible based on current checks. Excluded costs still need review.' : `Not feasible: ${(q.infeasibility_reasons || []).map(human).join(', ')}`}</p>
      <details><summary>Price, policy and exclusions</summary><QuoteCard quote={q} /></details>
      {onSelect && <button className="chat-choice" disabled={busy || !q.feasible || q.priced_total == null} onClick={() => onSelect(q)}>Select supplier</button>}
    </article>)}
  </div>;
}

const retailPrompt = {
  DRAFT: 'Choose a supplier so I can prepare a draft for you.',
  AWAITING_MANAGER_APPROVAL: 'Your draft is ready. Would you like to review the exact message and commercial terms?',
  REQUEST_QUEUED: 'Your approval is recorded. Shall I publish the approved request notification now?',
  AWAITING_SUPPLIER_APPROVAL: 'The request is waiting for the supplier’s review. I’ll show the next update here.',
  RESPONSE_QUEUED: 'The supplier has approved the response. Its notification is waiting to be published.',
  SUPPLIER_RESPONDED: 'The supplier response is available. Review it below, then complete this request when you’re ready.',
  COMPLETED: 'This request’s approval and notification workflow is complete.',
  MANAGER_REJECTED: 'This draft was rejected. Nothing will be sent from this draft. You can start a new assessment when you’re ready.',
  SUPPLIER_REJECTED: 'The supplier rejected this request. You can start a new assessment to consider another supplier.',
};

// Reuse the exact-draft and human-decision controls; only the presentation changes.
function RetailStep({ record, session, disabled, onChange }) {
  const [reviewing, setReviewing] = useState(false);
  return <>
    {record.status === 'DRAFT' && !disabled && <PrepareDraft record={record} onChange={onChange} />}
    {record.status === 'AWAITING_MANAGER_APPROVAL' && <>
      {!reviewing ? <button className="chat-choice" onClick={() => setReviewing(true)} disabled={disabled}>Review draft and terms</button> : <>
        {record.draft?.quote && <QuoteCard quote={record.draft.quote} />}
        <ManagerEmailReview record={record} session={session} onChange={onChange} onRefresh={onChange} disabled={disabled} compact />
      </>}
    </>}
  </>;
}

export default function ChatRequest({ id, session, onChange, onMessage }) {
  const resource = useResource(() => api.case(id), [id], 10000);
  const action = useAction();
  const [delivery, setDelivery] = useState('');
  const c = resource.data, retail = session.role === 'retail-manager';
  const stepEnd = useRef(null);
  useEffect(() => {
    if (c) stepEnd.current?.scrollIntoView({ block: 'nearest', behavior: 'auto' });
  }, [c?.case_id, c?.version]);
  function changed() { resource.reload(); onChange?.(); }
  async function dispatch() {
    const result = await action.run('dispatch', () => api.dispatch(id, 'request'));
    if (result) {
      setDelivery(result.delivery_status || '');
      onMessage?.('The notification service returned a result. The request status below shows the latest saved outcome.');
      changed();
    }
  }
  async function complete() {
    const result = await action.run(`complete:${id}`, (key) => api.complete(id, key));
    if (result) { onMessage?.('The completed request summary has been saved.'); changed(); }
  }
  if (!c) return <WorkflowReply label="Request review in chat" step="Loading your request">
    {resource.loading && <Loading />}<ErrorNotice error={resource.error} retry={resource.reload} />
  </WorkflowReply>;
  const unsafe = Boolean(resource.error) || action.busy;
  const uncertain = c.notifications?.some((n) => n.type === 'SUPPLIER_REQUEST' && ['FAILED', 'UNKNOWN', 'SENDING'].includes(n.status));
  return <WorkflowReply label="Request review in chat" step={retail ? 'Retail agent · Next step' : 'Supplier agent · Next step'}>
    <div className="chat-request-line"><strong>{c.item_id} · {number(c.recommended_quantity)} units</strong><Status value={c.status} /></div>
    <small className="muted">Request #{id.slice(0, 8)} · {c.selected_supplier_id || 'Choose a supplier'}</small>
    <ErrorNotice error={resource.error} retry={resource.reload} />
    {retail && <p className="chat-step-prompt">{retailPrompt[c.status] || 'Review the latest request status before continuing.'}</p>}
    <details className="chat-saved-details"><summary>Saved request and progress</summary>
      <p>Request {id} · Version {c.version}</p>
      {c.draft?.quote && <QuoteCard quote={c.draft.quote} />}
      {retail && !['DRAFT', 'AWAITING_MANAGER_APPROVAL'].includes(c.status) && <EmailSnapshot email={c.draft?.supplier_email} title="Saved retail request" approved={c.status !== 'MANAGER_REJECTED'} rejected={c.status === 'MANAGER_REJECTED'} />}
      <CaseProgress record={c} />
    </details>
    {retail ? <RetailStep key={`${id}:${c.version}:${c.draft_hash}`} record={c} session={session} disabled={unsafe} onChange={changed} />
      : <SupplierWorkflow record={c} session={session} onChange={changed} Decision={DecisionPanel} disabled={unsafe} conversational />}
    {retail && c.status === 'REQUEST_QUEUED' && <>
      {uncertain && <p role="alert">Publication needs reconciliation. Do not send again until its outcome is verified.</p>}
      <button className="chat-choice" disabled={unsafe || uncertain} onClick={dispatch}>Dispatch approved email</button>
    </>}
    {retail && ['RESPONSE_QUEUED', 'SUPPLIER_RESPONDED', 'SUPPLIER_REJECTED', 'COMPLETED'].includes(c.status) &&
      <details open={c.status === 'SUPPLIER_RESPONDED'}><summary>Read supplier response</summary>
        <EmailSnapshot email={c.draft?.supplier_response} title="Supplier response preview" approved={c.status !== 'SUPPLIER_REJECTED'} rejected={c.status === 'SUPPLIER_REJECTED'} />
      </details>}
    {retail && c.status === 'SUPPLIER_RESPONDED' && <button className="chat-choice" disabled={unsafe} onClick={complete}>Complete request</button>}
    {retail && <ApprovedSummary record={c} />}
    <ErrorNotice error={action.error} />
    {delivery && <p role="status">Delivery status: {human(delivery)}. Publication does not confirm inbox delivery.</p>}
    <button className="text-button chat-refresh" onClick={resource.reload} disabled={action.busy}><Icon name="refresh" size={13} /> Refresh status</button>
    <div ref={stepEnd} />
  </WorkflowReply>;
}
