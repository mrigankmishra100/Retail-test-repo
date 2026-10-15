import { useState } from 'react';
import { apiLabel, fileSearchLabel, controlDescription } from './execution-labels';

const timing = (value) => Number.isFinite(value) ? `${(value / 1000).toFixed(2)} s` : null;
const readable = (value) => String(value ?? 'unknown').replaceAll('_', ' ');

export default function ExecutionFlow({ metrics, compact = false }) {
  const [reviewStep, setReviewStep] = useState(null);
  let modelCall = 0;
  const events = metrics.events || [];
  const steps = events.filter((event) => ['model', 'model_tool_request', 'function_tool', 'built_in_tool', 'mcp_safety', 'mcp_result', 'chat'].includes(event.stage));
  const mapping = events.find((event) => event.stage === 'agent_mapping');
  const toolNames = [...new Set(events.filter((event) => ['function_tool', 'built_in_tool'].includes(event.stage)).map((event) => event.tool).filter(Boolean))];
  const retrieval = events.find((event) => event.stage === 'retrieval');
  return <section className="execution-flow" aria-label="Recorded execution flow">
    <div className="flow-heading"><div>{!compact && <span className="eyebrow">EXECUTION MAP</span>}<h4>{compact ? 'Execution steps' : 'How this answer was produced'}</h4></div>
      {steps.length > 0 && <button type="button" onClick={() => setReviewStep(reviewStep === null ? 0 : null)}>{reviewStep === null ? 'Step through' : 'Show all steps'}</button>}
    </div>
    {!compact && <p className="flow-caption">Recorded after execution · Model timings include provider-executed tools</p>}
    <div className="flow-context">
      {mapping && <span>Agent: {mapping.tool || 'retail'} · {readable(mapping.status)}</span>}
      {(!compact || toolNames.length > 0) && <span>Tools called: {toolNames.length ? toolNames.join(', ') : 'None recorded'}</span>}
      {retrieval && (!compact || retrieval.status === 'enabled') && <span>File Search: {readable(retrieval.status)}</span>}
    </div>
    {!steps.length && <p className="flow-caption">No execution steps were recorded for this run.</p>}
    <ol className="flow-steps">{steps.map((event, index) => {
      const title = event.stage === 'model' ? `Model call ${++modelCall}` : event.stage === 'chat' ? 'Run outcome' : event.tool || 'Tool call';
      const kind = event.stage === 'model' ? 'MODEL' : event.stage === 'chat' ? 'RESULT' : event.stage === 'model_tool_request' ? `${apiLabel(event.api_source)} · REQUESTED TOOL` : event.stage === 'built_in_tool' ? fileSearchLabel(event.api_source) : event.stage === 'mcp_safety' ? 'LOCAL MCP SAFETY' : event.stage === 'mcp_result' ? 'MCP RESULT VALIDATION' : 'TOOL CALLED';
      return <li key={index} className={`flow-step ${reviewStep === index ? 'is-selected' : ''} ${['failed', 'blocked'].includes(event.status) ? 'has-failed' : ''}`} aria-current={reviewStep === index ? 'step' : undefined}>
        <span className="flow-number" aria-hidden="true">{index + 1}</span><div><small>{kind}</small><strong>{title}</strong><span>{readable(event.status)}</span>{controlDescription(event) && <details><summary>Check details</summary><p className="flow-caption">{controlDescription(event)}</p></details>}</div><time>{timing(event.duration_ms)}</time>
      </li>;
    })}</ol>
    {reviewStep !== null && <div className="flow-review"><p role="status">Reviewing step {reviewStep + 1} of {steps.length}. This does not execute the model again.</p><div>
      <button type="button" disabled={reviewStep === 0} onClick={() => setReviewStep((step) => step - 1)}>Previous step</button>
      <button type="button" disabled={reviewStep >= steps.length - 1} onClick={() => setReviewStep((step) => step + 1)}>Next step</button>
    </div></div>}
  </section>;
}
