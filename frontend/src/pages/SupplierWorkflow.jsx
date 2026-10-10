import { useState } from 'react';
import api from '../api';
import { ErrorNotice, Loading, Status, human, number, useAction, useResource } from '../ui';
import { EmailSnapshot, ApprovedSummary } from './CaseProgress';
import { QuoteCard } from './Workspace';

export default function SupplierWorkflow({ record, session, onChange, Decision, disabled = false, conversational = false }) {
  const storageKey = `supplier.thread.${session.session_id}.${session.supplier_id}.${record.case_id}`;
  const [thread, setThread] = useState(() => sessionStorage.getItem(storageKey));
  const [reviewedDraft, setReviewedDraft] = useState(null);
  const action = useAction();
  const resource = useResource(
    () => (thread ? api.supplierWorkflow(thread) : Promise.resolve(null)),
    [thread],
    thread && !action.busy ? 10000 : 0,
  );
  const workflow = resource.data;
  // Parent case polling can be newer than the workflow checkpoint.
  const c = workflow?.case && workflow.case.version >= record.version ? workflow.case : record;
  const response = c.draft?.supplier_response;
  const waiting = c.status === 'AWAITING_SUPPLIER_APPROVAL';
  const uncertain =
    workflow?.status === 'delivery_reconciliation_required' ||
    c.notifications?.some(
      (n) => n.type === 'SUPPLIER_RESPONSE' && ['FAILED', 'UNKNOWN'].includes(n.status),
    );
  const approved = ['RESPONSE_QUEUED', 'SUPPLIER_RESPONDED', 'COMPLETED'].includes(c.status);
  const reviewKey = `${c.case_id}:${c.version}:${c.draft_hash}`;
  const showReview = !conversational || (waiting && reviewedDraft === reviewKey);
  function remember(id) {
    setThread(id);
    if (id) sessionStorage.setItem(storageKey, id);
    else sessionStorage.removeItem(storageKey);
  }
  async function start() {
    const result = await action.run(`supplier-start:${c.case_id}`, (key) =>
      api.startSupplierWorkflow(c.case_id, key),
    );
    if (result) {
      remember(result.thread_id);
      onChange();
    }
  }
  async function resume() {
    const result = await action.run(`supplier-resume:${thread}`, () =>
      api.resumeSupplierWorkflow(thread),
    );
    if (result) {
      resource.reload();
      onChange();
    }
  }
  function decided() {
    resource.reload();
    onChange();
  }
  return (
    <section className={`supplier-agent ${conversational ? 'supplier-chat-step' : ''}`} aria-label="Supplier agent workflow">
      <div className="section-heading">
        <div>
          {!conversational && <h3>Supplier agent review</h3>}
          <p>{conversational ? (approved ? 'Your approval is saved. The next step is shown below.' : 'I’ll check stock and policy terms, then help you review the response.') : 'Check availability and terms, review the response, then record your decision.'}</p>
        </div>
        <Status value={c.status === 'COMPLETED' ? 'completed' : workflow?.status || 'ready'} />
      </div>
      <ErrorNotice error={action.error} />
      <ErrorNotice error={resource.error} retry={resource.reload} />
      {resource.error?.status === 404 && (
        <div className="callout">
          <p>This saved workflow expired. The case and recorded approvals remain available.</p>
          <button onClick={() => remember(null)}>Clear expired workflow</button>
        </div>
      )}
      {resource.loading && thread && <Loading label="Loading supplier review…" />}
      {workflow && (
        <>
          <p>{workflow.message}</p>
          {workflow.error_code && <p role="status">Review status: {human(workflow.error_code)}</p>}
          <div className="supplier-checks">
            <div>
              <h4>Available stock</h4>
              <p>
                {workflow.inventory
                  ? `${number(workflow.inventory.available_quantity)} units`
                  : 'Not yet verified'}
              </p>
            </div>
            <div>
              <h4>Supplier policy</h4>
              <p>{workflow.policy ? 'Retrieved for this review' : 'Not yet verified'}</p>
            </div>
            <div>
              <h4>Quote</h4>
              <p>
                {workflow.quote
                  ? workflow.quote.feasible
                    ? 'Feasible quote retrieved'
                    : 'Cannot fulfill current terms'
                  : 'Not yet verified'}
              </p>
            </div>
          </div>
          {workflow.policy && (
            <details className="policy-review">
              <summary>Read supplier policy</summary>
              <pre>{workflow.policy.content}</pre>
            </details>
          )}
          {workflow.quote && showReview && <QuoteCard quote={workflow.quote} />}
        </>
      )}
      {showReview && <EmailSnapshot
        email={response}
        title="Supplier response preview"
        approved={approved}
        rejected={c.status === 'SUPPLIER_REJECTED'}
      />}
      {conversational && approved && <details><summary>Read approved response</summary><EmailSnapshot email={response} title="Approved supplier response" approved /></details>}
      {!response && (
        <p className="callout">
          This older case has no saved supplier response. Create a fresh retail draft to review and
          approve a response email.
        </p>
      )}
      {!thread && c.status !== 'COMPLETED' && c.status !== 'SUPPLIER_REJECTED' && (
        <div className="action-box">
          <p>
            {approved
              ? 'A human decision is already recorded. Continuing lets the agent publish the approved response and finalize the case.'
              : 'Start the agent checks. It pauses for your approval before sending a response.'}
          </p>
          <button className="primary" disabled={disabled || action.busy || uncertain} onClick={start}>
            {action.busy
              ? 'Checking…'
              : approved
                ? 'Continue approved supplier case'
                : 'Start supplier review'}
          </button>
        </div>
      )}
      {conversational && thread && workflow && waiting && !showReview && <button className="chat-choice" disabled={disabled || resource.loading || Boolean(resource.error)} onClick={() => setReviewedDraft(reviewKey)}>Review response and terms</button>}
      {thread && workflow && waiting && showReview && (
        <Decision
          key={`${c.case_id}:${c.version}:${c.draft_hash}`}
          record={c}
          session={session}
          onChange={decided}
          reviewReady={Boolean(
              !disabled &&
            response &&
            !resource.loading &&
            !resource.error &&
            !action.busy &&
            workflow.status === 'awaiting_approval' &&
            workflow.case.version === c.version &&
            workflow.case.draft_hash === c.draft_hash,
          )}
          compact={conversational}
        />
      )}
      {workflow?.status === 'review_blocked' && (
        <p className="callout">
          Approval is blocked by the agent checks. You can reject the request, or correct the
          underlying stock or policy and run the checks again.
        </p>
      )}
      {thread &&
        workflow?.can_resume &&
        !uncertain &&
        c.status !== 'COMPLETED' &&
        (!waiting || workflow.pending_input === 'review') && (
          <div className="action-box">
            <p>
              {approved
                ? 'Your approval is recorded. The agent can now send the approved response and save the completed summary.'
                : 'Continue the workflow using the latest case and supplier facts.'}
            </p>
            <button
              className="primary"
              disabled={disabled || action.busy || resource.loading || Boolean(resource.error)}
              onClick={resume}
            >
              {action.busy
                ? 'Continuing…'
                : approved
                  ? 'Send approved response & complete'
                  : workflow.pending_input === 'review'
                    ? 'Recheck supplier facts'
                    : 'Continue supplier workflow'}
            </button>
          </div>
        )}
      {uncertain && (
        <p className="callout" role="alert">
          Response publication needs operator reconciliation. The agent will not automatically
          resend it.
        </p>
      )}
      <ApprovedSummary record={c} />
    </section>
  );
}
