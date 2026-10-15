import { Status, number, human, money } from '../ui';
import { ChatQuotes } from './ChatWorkflow';
import Markdown from '../Markdown';

export default function WorkflowHistory({ run }) {
  const snapshot = run.workflow_snapshot;
  if (!snapshot) return <div className="workflow-history"><h3>{run.label}</h3>
    {run.client_state === 'failed' && <p>The action failed. No successful decision or transition is confirmed by this response.</p>}
    {run.client_state === 'interrupted' && <p>The outcome is unconfirmed. Refresh the saved request before retrying.</p>}
  </div>;
  const { record, comparison, email } = snapshot;
  return <section className="workflow-history" aria-label={snapshot.title}>
    <h3>{snapshot.title}</h3>
    {snapshot.message && <Markdown>{snapshot.message}</Markdown>}
    {record && <>
      <div className="workflow-history-facts">
        {record.item_id && <strong>{record.item_id}</strong>}
        {record.selected_supplier_id && <span>{record.quote?.supplier_name || record.selected_supplier_id} · {record.selected_supplier_id}</span>}
        {record.recommended_quantity != null && <span>{number(record.recommended_quantity)} units</span>}
        {record.quote?.priced_total != null && <span>{money(record.quote.priced_total)}</span>}
        {record.status && <Status value={record.status} />}
      </div>
      <small>Request #{record.case_id?.slice(0, 8)}{record.version != null ? ` · Version ${record.version}` : ''} · Saved at this step</small>
    </>}
    {comparison && <details><summary>Supplier comparison · {comparison.item_id} · {number(comparison.quantity)} units</summary>
      <ChatQuotes quotes={comparison.suppliers} recommendedSupplierId={comparison.recommended_supplier_id} />
    </details>}
    {email && <details><summary>Draft returned at this step</summary><strong>{email.subject}</strong><pre>{email.body}</pre></details>}
    {snapshot.delivery_status && <p>Publication status: {human(snapshot.delivery_status)}</p>}
  </section>;
}
