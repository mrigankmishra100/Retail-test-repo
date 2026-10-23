import { useState } from 'react';
import api from '../api';
import EstimatedFinOps from './EstimatedFinOps';
import { ErrorNotice, useResource } from '../ui';

const text = (value, fallback = '—') => typeof value === 'string' && value.length ? value :
  typeof value === 'number' && Number.isFinite(value) ? String(value) : fallback;
const label = (value) => text(value, 'unavailable').replaceAll('_', ' ');
const isError = (event) => ['ERROR', 'CRITICAL'].includes(event.level) ||
  ['failed', 'blocked', 'error'].includes(event.status) || Number(event.http_status ?? event.status) >= 400;
const eventTime = (value) => value && !Number.isNaN(Date.parse(value)) ? new Date(value).toLocaleString() : '—';

export default function CloudOperations({ active, records, models }) {
  const [revision, refresh] = useState(0);
  const logs = useResource(() => active ? api.centralLogs() : Promise.resolve(null), [active, revision]);
  const tracing = useResource(() => active ? api.tracing() : Promise.resolve(null), [active, revision]);
  const events = logs.data?.events || [];
  const available = logs.data?.search_status === 'available';
  return <div className="cloud-operations">
    <div className="evaluation-subheading"><h3>Cloud monitoring</h3>
      <button onClick={() => refresh((v) => v + 1)} disabled={!active || logs.loading}>Refresh cloud data</button></div>
    <div className="cloud-operations-grid">
      <EstimatedFinOps records={records} models={models} />
      <article><span className="eyebrow">OCI LOGGING</span><h4>Application logs</h4>
        <ErrorNotice error={logs.error} retry={logs.reload} />
        <p role="status">{logs.loading ? 'Reading OCI Logging…' : available ? 'Live records retrieved from OCI' : label(logs.data?.search_status)}</p>
        <div className="cloud-log-counts">
          <span><b>{available ? events.length : '—'}</b> retrieved events</span>
          <span><b>{available ? events.filter(isError).length : '—'}</b> errors / blocks</span>
          <span><b>{available ? new Set(events.map((event) => event.trace_id).filter(Boolean)).size : '—'}</b> trace IDs</span>
        </div>
        <small>Counts cover the retrieved events, across app sessions.</small>
        {logs.data?.region && <small>Region: {logs.data.region} · Updated {eventTime(logs.data.fetched_at)}</small>}
        <details className="log-delivery-details"><summary>Log delivery & source</summary>
          <p>{logs.data?.source || 'OCI Logging Search API'} · {logs.data?.scope || 'Configured application log'}</p>
          <p>{logs.data?.message}</p>
          <small>This API process: {logs.data?.accepted_events ?? '—'} accepted by OCI · {logs.data?.queued_events ?? '—'} queued · {logs.data?.dropped_events ?? '—'} dropped. Counters reset on restart.</small>
          <small>Application event metadata only. Prompts, answers, SQL and credentials are excluded.</small>
        </details>
      </article>
    </div>
    <section className="langfuse-tracing" aria-label="Langfuse tracing">
      <div className="evaluation-subheading"><h4>Langfuse tracing</h4>
        {tracing.data?.status === 'configured' && /^https?:\/\//i.test(tracing.data?.dashboard_url || '') &&
          <a href={tracing.data.dashboard_url} target="_blank" rel="noopener noreferrer">Open Langfuse</a>}
      </div>
      <ErrorNotice error={tracing.error} retry={tracing.reload} />
      <p>{tracing.loading ? 'Reading tracing configuration...' : tracing.data ? `${label(tracing.data.status)} - ${tracing.data.scope}` : 'Tracing status unavailable'}</p>
      <p className="muted">{tracing.data?.message}</p>
    </section>
  </div>;
}
