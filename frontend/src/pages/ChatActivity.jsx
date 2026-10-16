import { useEffect, useRef, useState } from 'react';
import { observeActivity, clearActivity } from '../activity-client';
import './chat-activity.css';
import { apiLabel, fileSearchLabel, controlDescription } from './execution-labels';

const words = (value) => String(value ?? '').replaceAll('_', ' ');
const timing = (ms) => `${(Math.max(0, ms || 0) / 1000).toFixed(1)} s`;
const titles = {
  get_inventory_risk: 'Checking inventory risk', get_suppliers_for_item: 'Finding suppliers',
  get_supplier_policy: 'Reading supplier policy', get_supplier_inventory: 'Checking supplier stock',
  calculate_supplier_quote: 'Calculating supplier quote', get_chat_history: 'Loading conversation',
  save_chat_turn: 'Saving conversation', query_retail_data: 'Querying retail data',
  search_policy_documents: 'Searching policy documents', file_search: 'File Search',
  'retail.analyze': 'Assessing inventory', 'retail.compare': 'Comparing suppliers',
  'retail.explain': 'Preparing recommendation', 'retail.select': 'Supplier selection',
  'retail.approval': 'Manager approval', 'supplier.approval': 'Supplier approval',
  'retail.create': 'Creating request draft', 'retail.prepare': 'Preparing draft and terms',
  'retail.dispatch': 'Publishing approved request', 'retail.wait_delivery': 'Waiting for publication status',
  'supplier.receive': 'Loading assigned request', 'supplier.inventory': 'Checking supplier stock',
  'supplier.policy': 'Checking policy terms', 'supplier.quote': 'Verifying quote',
  'supplier.draft': 'Preparing supplier response', 'supplier.dispatch': 'Publishing approved response',
  'supplier.complete': 'Completing case', 'supplier.memory': 'Loading approved summary',
  'supplier.wait_review': 'Waiting for review', 'supplier.wait_delivery': 'Waiting for publication status',
};
function stepTitle(step) {
  if (step.stage === 'topic_scope' && step.name === 'topic_scope') return `Retail app relevance · ${words(step.status)}`;
  if (step.stage === 'tool_validation') return `${step.name} · invalid arguments`;
  if (step.stage === 'model_tool_request') return `${apiLabel(step.api_source)} requested · ${step.name}`;
  if (step.stage === 'built_in_tool') return fileSearchLabel(step.api_source);
  if (step.stage === 'mcp_safety') return `Local MCP safety · ${step.name}`;
  if (step.stage === 'mcp_result') return `MCP result validation · ${step.name}`;
  if (['tool', 'function_tool'].includes(step.stage)) return `Tool called · ${step.name}`;
  if (step.stage === 'agent_mapping') return `${words(step.name)} agent · ${words(step.status)}`;
  if (step.stage === 'tool_transport') return `Tool transport · ${words(step.status)}`;
  if (step.stage === 'retrieval') return `File Search ${words(step.status)}`;
  if (step.stage === 'chat') return `Answer outcome · ${words(step.status)}`;
  return titles[step.name] || words(step.name);
}

function ActivityRun({ run, answerStatus, guardrailStatus }) {
  const container = useRef(null);
  const running = run.client_state === 'running';
  useEffect(() => { if (running) container.current?.scrollIntoView({ block: 'nearest', behavior: 'auto' }); }, []);
  const [elapsed, setElapsed] = useState(run.elapsed_ms || 0);
  useEffect(() => {
    setElapsed(run.elapsed_ms || 0);
    if (!running) return;
    const since = performance.now();
    const timer = setInterval(() => setElapsed((run.elapsed_ms || 0) + performance.now() - since), 100);
    return () => clearInterval(timer);
  }, [running, run.elapsed_ms]);
  const steps = (run.steps || []).filter((step) => step.stage !== 'tool_transport');
  const current = [...steps].reverse().find((step) => step.status === 'running');
  const answerOutcome = answerStatus || [...steps].reverse().find((step) => step.stage === 'chat')?.status;
  const securityOutcome = ['FAILED', 'BLOCKED'].includes(guardrailStatus) ? guardrailStatus.toLowerCase()
    : [...steps].reverse().find((step) => step.stage === 'security' && ['failed', 'blocked'].includes(step.status))?.status;
  const unavailable = ['unavailable', 'disabled', 'limit_reached'].includes(answerOutcome);
  const failed = run.client_state === 'failed' || run.status === 'failed' || unavailable || answerOutcome === 'blocked' || Boolean(securityOutcome);
  const interrupted = run.client_state === 'interrupted';
  const duration = run.duration_ms ?? elapsed;
  const summary = running ? (current ? stepTitle(current) : 'Working')
    : interrupted ? 'Connection interrupted' : securityOutcome === 'failed' ? 'Guardrails failed' : answerOutcome === 'blocked' || securityOutcome === 'blocked' ? 'Guardrails blocked' : unavailable ? 'Answer unavailable' : failed ? 'Request failed' : 'Finished';
  return <details ref={container} className={`chat-activity-run ${running ? 'is-running' : ''}`} onToggle={(event) => {
    if (event.currentTarget.open) event.currentTarget.scrollIntoView({ block: 'nearest', behavior: 'auto' });
  }}>
    <summary><span className={`activity-indicator ${failed || interrupted ? 'has-error' : ''}`} aria-hidden="true" />
      <span className="activity-summary"><strong>{summary} · {timing(running ? elapsed : duration)}</strong></span>
      <span className="activity-step-count">{steps.length ? `${steps.length} steps` : 'Details'}</span>
      <span className="activity-chevron" aria-hidden="true">›</span>
    </summary>
    <div className="activity-content">
      <p>{running ? 'Live activity' : 'Recorded activity'} · Expand a step for details.</p>
      {!steps.length && <p>{running ? 'Waiting for execution events…' : 'Detailed activity is unavailable for this request. Its result is shown in the chat.'}</p>}
      <ol>{steps.map((step) => <li key={step.id} className={`activity-step ${step.status}`}>
        <span className="activity-step-dot" aria-hidden="true" />
        <details><summary><span>{stepTitle(step)}</span>{!['agent_mapping', 'tool_transport', 'retrieval', 'chat', 'model_tool_request'].includes(step.stage) && <small>{step.status === 'running' && !running ? 'Outcome unreported' : words(step.status)}</small>}<time>{step.duration_ms != null ? timing(step.duration_ms) : ''}</time></summary>
          <div><code>{step.name}</code><span>{words(step.stage)} · started +{timing(step.offset_ms)}</span>{step.service && <span>Recorded by {step.service} · received when the service returned.</span>}{step.stage === 'model_tool_request' && <span>The model requested this tool. The application checks permissions and arguments before executing it.</span>}{controlDescription(step) && <span>{controlDescription(step)}</span>}</div>
        </details>
      </li>)}</ol>
      {run.truncated && <p>Showing the first 200 activity steps.</p>}
      {interrupted && <p role="status">The connection ended before the outcome was confirmed. Check the saved request before retrying.</p>}
      <p className="activity-footnote">Operational events only. Private model reasoning is not displayed.</p>
    </div>
  </details>;
}

export function useActivityRuns(active, resetKey, initialRuns = []) {
  const [runs, setRuns] = useState(initialRuns);
  const lastReset = useRef(resetKey);
  useEffect(() => { if (lastReset.current !== resetKey) setRuns([]); lastReset.current = resetKey; clearActivity(); }, [resetKey]);
  useEffect(() => {
    if (!active) return;
    return observeActivity((run) => setRuns((current) => {
      const existing = current.some((entry) => entry.id === run.id);
      return (existing ? current.map((entry) => entry.id === run.id ? run : entry) : [...current, run]);
    }));
  }, [active]);
  return runs;
}

export default function ChatActivity({ runs = [], answerStatus, guardrailStatus }) {
  if (!runs.length) return null;
  return <section className="chat-activity" aria-label="Agent execution activity">
    <span className="sr-only" role="status">{runs.some((run) => run.client_state === 'running') ? 'Agent request in progress' : 'Agent request finished'}</span>
    {runs.map((run) => <ActivityRun key={run.id} run={run} answerStatus={answerStatus} guardrailStatus={guardrailStatus} />)}
  </section>;
}
