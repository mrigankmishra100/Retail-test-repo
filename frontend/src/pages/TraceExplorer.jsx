import { useState } from 'react';
import ExecutionFlow from './ExecutionFlow';
import { outcome, needsAttention, modelError } from './model-status';
import SecurityDecision from './SecurityDecision';
import QueryDetails from './QueryDetails';

const text = (value, fallback = '—') => typeof value === 'string' || typeof value === 'number' ? String(value) : fallback;
const label = (value) => text(value, 'unavailable').replaceAll('_', ' ');
const duration = (value) => typeof value === 'number' && Number.isFinite(value) ? `${(value / 1000).toFixed(2)} s` : '—';
const count = (value) => typeof value === 'number' && Number.isFinite(value) ? value.toLocaleString() : '—';
const time = (value) => value && Number.isFinite(Date.parse(value)) ? new Date(value).toLocaleTimeString() : '—';

export default function TraceExplorer({ records, models, loading, refresh }) {
  const [query, setQuery] = useState(''), [status, setStatus] = useState('all'), [selected, setSelected] = useState(null);
  const modelName = (metrics) => models.find((m) => m.id === metrics.profile_id)?.label || text(metrics.profile_id || metrics.model_id);
  const runs = records.filter((record) => {
    const metrics = record.metrics;
    if (!metrics) return false;
    const searchable = [record.id, modelName(metrics), metrics.model_id, metrics.status,
      ...(metrics.events || []).flatMap((event) => [event.stage, event.tool, event.status])].join(' ').toLowerCase();
    return searchable.includes(query.toLowerCase()) && (status === 'all' || (status === 'attention' ? needsAttention(metrics) : outcome(metrics) === status));
  });
  const current = runs.find((run) => run.id === selected) || runs[0];
  const metrics = current?.metrics;
  return <section className="application-events trace-explorer" aria-label="Execution trace explorer">
    <div className="evaluation-subheading"><h3>Execution traces</h3><button onClick={refresh} disabled={loading}>{loading ? 'Refreshing…' : 'Refresh traces'}</button></div>
    <div className="trace-toolbar">
      <label className="eval-field">Search traces<input type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Model, tool, status or run ID" /></label>
      <label className="eval-field">Run status<select value={status} onChange={(e) => setStatus(e.target.value)}><option value="all">All statuses</option><option value="completed">Completed</option><option value="attention">Needs attention</option></select></label>
      <span className="trace-result-count" role="status">{runs.length} matching runs</span>
    </div>
    <div className={`trace-layout ${current ? 'has-selection' : ''}`}>
      <div className="events-table trace-run-list"><table><colgroup><col style={{width:'43%'}} /><col style={{width:'19%'}} /><col style={{width:'15%'}} /><col style={{width:'23%'}} /></colgroup><thead><tr><th>Trace / model</th><th>Outcome</th><th>Latency</th><th>Tokens in / out</th></tr></thead><tbody>
        {runs.map((run) => <tr key={run.id} className={run.id === current?.id ? 'selected-trace' : ''}>
          <td><button className="trace-select" onClick={() => setSelected(run.id)} aria-pressed={run.id === current?.id} aria-label={`Inspect trace ${run.id}`}><strong>{modelName(run.metrics)}</strong><span><code>{text(run.id).slice(0, 8)}</code> · {time(run.metrics.events?.[0]?.time)}</span></button></td>
          <td><span className={`trace-badge ${needsAttention(run.metrics) ? 'attention' : run.metrics.status === 'completed' ? 'success' : ''}`}>{label(outcome(run.metrics))}</span></td>
          <td>{duration(run.metrics.duration_ms)}</td><td>{count(run.metrics.input_tokens)} / {count(run.metrics.output_tokens)}</td>
        </tr>)}
      </tbody></table>{!runs.length && <div className="empty-events"><strong>{loading ? 'Loading traces…' : 'No matching traces'}</strong><p>{query || status !== 'all' ? 'Try another search or clear the status filter.' : 'Ask the agent a question or run a model comparison to record an execution.'}</p></div>}</div>
      {metrics && <section className="trace-detail" aria-label="Selected trace details">
        <div className="trace-detail-heading"><h4>{modelName(metrics)}</h4><span className={`trace-badge ${needsAttention(metrics) ? 'attention' : metrics.status === 'completed' ? 'success' : ''}`}>{label(outcome(metrics))}</span></div>
        {outcome(metrics) === 'ai_unavailable' && <p className="callout" role="status">The workflow continued, but its AI request failed.</p>}
        {metrics.calls?.filter((call) => call.error_code).map((call) => <p role="status" key={call.index}>{modelError(call)}{call.http_status ? ` (HTTP ${call.http_status})` : ''}</p>)}
        <dl className="trace-facts"><div><dt>Duration</dt><dd>{duration(metrics.duration_ms)}</dd></div><div><dt>Model calls</dt><dd>{count(metrics.call_count)}</dd></div><div><dt>Input tokens</dt><dd>{count(metrics.input_tokens)}</dd></div><div><dt>Output tokens</dt><dd>{count(metrics.output_tokens)}</dd></div></dl>
        {metrics.security_checks?.filter((check) => ['BLOCKED', 'FAILED'].includes(check.status)).map((check, i) => <SecurityDecision key={i} details={check} />)}
        <QueryDetails queries={metrics.queries} />
        <ExecutionFlow key={current.id} metrics={metrics} compact />
        <details className="trace-raw"><summary>Recorded events · {metrics.events?.length || 0}</summary>
          <code className="trace-id">{text(current.id)}</code>
          <ol>{metrics.events?.map((event, i) => <li key={i}><time>{time(event.time)}</time><strong>{label(event.tool || event.stage)}</strong><span>{label(event.status)} · {duration(event.duration_ms)}</span></li>)}</ol>
          <p className="evaluation-note">Tokens are provider-reported. Timings are measured by the application.</p>
        </details>
        {!!metrics.citations?.length && <div className="trace-sources"><h4>Retrieved sources</h4><ul>{metrics.citations.map((source, i) => <li key={i}>{text(source.filename || source.file_id, 'Policy document')}</li>)}</ul></div>}
      </section>}
    </div>
  </section>;
}
