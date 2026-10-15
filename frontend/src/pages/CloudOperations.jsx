import { useState } from 'react';
import api from '../api';
import EstimatedFinOps from './EstimatedFinOps';
import { ErrorNotice, useResource } from '../ui';
import { apiLabel } from './execution-labels';

const text = (value, fallback = '—') => typeof value === 'string' && value.length ? value :
  typeof value === 'number' && Number.isFinite(value) ? String(value) : fallback;
const label = (value) => text(value, 'unavailable').replaceAll('_', ' ');
const isError = (event) => ['ERROR', 'CRITICAL'].includes(event.level) ||
  ['failed', 'blocked', 'error'].includes(event.status) || Number(event.http_status ?? event.status) >= 400;
const eventName = (event) => event.stage === 'model_tool_request' ? `${apiLabel(event.api_source)} requested ${text(event.mcp_tool)}` :
  event.mcp_tool ? `Tool · ${text(event.mcp_tool)}` : event.graph_node ? `Workflow · ${text(event.graph_node)}` :
    event.stage ? label(event.stage) : event.route ? `${text(event.method, 'HTTP')} ${event.route}` : event.event === 'request_completed' || event.logger === 'app.http' ? 'API request completed' : label(event.event || event.logger);
const eventTime = (value) => value && !Number.isNaN(Date.parse(value)) ? new Date(value).toLocaleString() : '—';

export default function CloudOperations({ active, records, models }) {
  const [revision, refresh] = useState(0), [hours, setHours] = useState(1);
  const [query, setQuery] = useState(''), [filter, setFilter] = useState('all'), [selected, setSelected] = useState(null);
  const logs = useResource(() => active ? api.centralLogs(hours) : Promise.resolve(null), [active, revision, hours]);
  const events = logs.data?.events || [];
  const visible = events.filter((event) => (filter !== 'errors' || isError(event)) &&
    `${eventName(event)} ${Object.values(event).map((value) => text(value, '')).join(' ')}`.toLowerCase().includes(query.toLowerCase()));
  const available = logs.data?.search_status === 'available';
  return <div className="cloud-operations">
    <div className="evaluation-subheading"><h3>Cloud monitoring</h3>
      <button onClick={() => { setSelected(null); refresh((v) => v + 1); }} disabled={!active || logs.loading}>Refresh cloud data</button></div>
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
    <section className="cloud-log-details" aria-label="OCI application events">
      <div className="cloud-log-toolbar"><h4>OCI application events</h4>
        <label>Time range<select aria-label="Log time range" value={hours} onChange={(e) => { setHours(Number(e.target.value)); setSelected(null); }}>
          <option value={1}>Last hour</option><option value={6}>Last 6 hours</option><option value={24}>Last 24 hours</option>
        </select></label>
        <label>Search logs<input type="search" placeholder="Tool, trace ID, model or service" value={query} onChange={(e) => { setQuery(e.target.value); setSelected(null); }} /></label>
        <label>Show<select aria-label="Log severity" value={filter} onChange={(e) => { setFilter(e.target.value); setSelected(null); }}><option value="all">All events</option><option value="errors">Errors / blocks</option></select></label>
      </div>
      {available && <p className="muted">{visible.length} of {events.length} retrieved events · Latest {logs.data?.limit || 50} maximum · Select an event for details.</p>}
      {visible.length > 0 && <div className="events-table"><table><thead><tr><th>Time</th><th>Event</th><th>Service / model / trace</th><th>Status</th></tr></thead>
        <tbody>{visible.map((event, i) => <tr key={i} className={selected === event ? 'is-selected' : ''}>
          <td>{eventTime(event.timestamp)}</td>
          <td><button className="log-event-button" onClick={() => setSelected(selected === event ? null : event)} aria-expanded={selected === event}>{eventName(event)}</button><small>{text(event.level, '')}{event.duration_ms != null ? ` · ${text(event.duration_ms)} ms` : ''}</small></td>
          <td>{text(event.service || event.model_id)}<small>{text(event.trace_id, '').slice(0, 8)}</small></td>
          <td><span className={`trace-badge ${isError(event) ? 'failed' : ''}`}>{label(event.status ?? event.http_status)}</span></td>
        </tr>)}</tbody></table></div>}
      {!visible.length && !logs.loading && <p className="log-empty">{available ? events.length ? 'No events match these filters.' : 'No OCI events in this time range. Try a wider range or refresh after ingestion.' : logs.data?.message || 'OCI events are unavailable. Check the connection above.'}</p>}
      {selected && <div className="cloud-log-record"><div className="evaluation-subheading"><h4>Event details</h4><button onClick={() => setSelected(null)}>Close details</button></div>
        <dl>{Object.entries(selected).map(([key, value]) => <div key={key}><dt>{key.replaceAll('_', ' ')}</dt><dd>{text(value)}</dd></div>)}</dl>
      </div>}
    </section>
  </div>;
}
