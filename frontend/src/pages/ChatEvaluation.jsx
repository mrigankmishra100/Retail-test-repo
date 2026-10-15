import { useEffect, useMemo, useState } from 'react';
import CloudOperations from './CloudOperations';
import EstimatedFinOps, { modelCost, money } from './EstimatedFinOps';
import api from '../api';
import { ErrorNotice, useResource } from '../ui';
import ExecutionFlow from './ExecutionFlow';
import TraceExplorer from './TraceExplorer';
import './chat-evaluation.css';
import Markdown from '../Markdown';
import RegionalModelForm, { regionalId, initialRegionalModel } from './RegionalModelForm';
import { outcome, needsAttention, modelError } from './model-status';
import SecurityDecision from './SecurityDecision';
import QueryDetails from './QueryDetails';
import ExternalModelDialog from './ExternalModelDialog';

const numeric = (value) => typeof value === 'number' && Number.isFinite(value);
const count = (value) => numeric(value) ? value.toLocaleString() : 'Not reported';
const duration = (value) => numeric(value) ? `${(value / 1000).toFixed(2)} s` : 'Not reported';

export function RunMetrics({ metrics, compact = false }) {
  if (!metrics) return null;
  return <div className={`run-metrics ${compact ? 'compact' : ''}`}>
    {!compact && metrics.security_checks?.filter((check) => ['BLOCKED', 'FAILED'].includes(check.status)).map((check, i) => <SecurityDecision key={i} details={check} />)}
    {!compact && <QueryDetails queries={metrics.queries} />}
    {outcome(metrics) === 'ai_unavailable' && <p className="callout" role="status">The workflow continued, but its AI request failed. The selected model did not produce a recommendation.</p>}
    {metrics.persistence === 'unavailable' && <p role="status">Metrics storage is unavailable. This run is shown here but is missing from session totals.</p>}
    <div className="metric-strip">
      <span><small>Input / output tokens</small><b>{count(metrics.input_tokens)} / {count(metrics.output_tokens)}</b></span>
      <span><small>End-to-end latency</small><b>{duration(metrics.duration_ms)}</b></span>
      <span><small>Estimated model cost</small><b>{money(modelCost(metrics))}</b></span>
      <span><small>Model calls</small><b>{count(metrics.call_count)}</b></span>
    </div>
    <ExecutionFlow key={metrics.run_id} metrics={metrics} />
    <details><summary>Technical details & sources · {outcome(metrics)?.replaceAll('_', ' ')}</summary>
      <p className="muted">{metrics.model_id} · {metrics.call_count} model calls · {metrics.route}</p>
      {metrics.calls?.filter((c) => c.error_code).map((c) => <p role="status" key={c.index}>Call {c.index}: {modelError(c)}{c.http_status ? ` (HTTP ${c.http_status})` : ''}</p>)}
      <p className="muted">Execution events show what ran. Private model reasoning is not displayed.</p>
      <ol className="execution-events">{metrics.events?.map((e, i) => <li key={i}>
        <span className={`event-dot ${e.status === 'failed' ? 'failed' : ''}`} />
        <strong>{e.tool || e.stage.replaceAll('_', ' ')}</strong><span>{e.status.replaceAll('_', ' ')}</span>
        {numeric(e.duration_ms) && <small>{duration(e.duration_ms)}</small>}
      </li>)}</ol>
      {metrics.citations?.length > 0 && <div><b>Retrieved sources</b><ul>{metrics.citations.map((c, i) =>
        <li key={i}>{c.filename || c.file_id || 'Policy document'}</li>)}</ul></div>}
      <p className="muted">Cached tokens: {count(metrics.cached_tokens)} · Reasoning tokens: {count(metrics.reasoning_tokens)} (included in output)</p>
      <p className="muted">{metrics.model_cost_scope || "Estimates use configured token rates and exclude other services; not an OCI bill."}</p>
      <small>Run {metrics.run_id}</small>
    </details>
  </div>;
}

export default function ChatEvaluation({ catalog, active, refreshKey, sessionId, onModelsChanged }) {
  const [regionalEnabled, setRegionalEnabled] = useState(false), [regional, setRegional] = useState(initialRegionalModel);
  const [externalDialog, setExternalDialog] = useState(false), [connectedModels, setConnectedModels] = useState([]);
  const models = [...new Map([...(catalog?.models || []), ...connectedModels].map((model) => [model.id, model])).values(), ...(regionalEnabled ? [{ id: regionalId, label: `${regional.label} · ${regional.region}`, model_id: regional.model_id, file_search: regional.api_mode === 'responses' && Boolean(regional.vector_store_ids.trim()) }] : [])];
  const canEvaluate = catalog?.enabled || connectedModels.length > 0;
  const storageKey = `chat-comparison.${sessionId}`;
  const [selected, setSelected] = useState([]), [prompt, setPrompt] = useState('Which items are at risk of a stockout, and what should I review next?');
  const [terms, setTerms] = useState(''), [rag, setRag] = useState(false), [error, setError] = useState(null);
  const [jobId, setJobId] = useState(() => sessionStorage.getItem(storageKey)), [job, setJob] = useState(null);
  const [starting, setStarting] = useState(false), [ratingBusy, setRatingBusy] = useState(null);
  const [tab, setTab] = useState('overview');
  const tabs = [['overview', 'Overview'], ['compare', 'Model comparison'], ['traces', 'Execution traces'], ['cloud', 'Cloud monitoring']];
  const [pollRevision, setPollRevision] = useState(0);
  const history = useResource(() => active ? api.chatMetrics() : Promise.resolve(null), [active, refreshKey]);
  useEffect(() => {
    if (models.length && !selected.length) setSelected(models.slice(0, 2).map((m) => m.id));
  }, [catalog]);
  useEffect(() => {
    if (!active || !jobId) return;
    let cancelled = false, timer;
    const poll = async () => {
      try {
        const result = await api.chatEvaluation(jobId);
        if (cancelled) return;
        setJob(result);
        if (['queued', 'running'].includes(result.status)) timer = setTimeout(poll, 2500);
        else history.reload();
      } catch (e) { if (!cancelled) {
        setError(e);
        if (e.status === 404) { setJobId(null); sessionStorage.removeItem(storageKey); }
      } }
    };
    poll();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [active, jobId, pollRevision]);
  const records = history.data?.records?.filter((r) => r.kind === 'turn') || [];
  const byId = Object.fromEntries(records.map((r) => [r.id, r]));
  const summary = useMemo(() => {
    const runs = records.map((r) => r.metrics).filter(Boolean);
    const latencies = runs.map((r) => r.duration_ms).filter(numeric).sort((a, b) => a - b);
    const percentile = (p) => latencies.length ? latencies[Math.max(0, Math.ceil(p * latencies.length) - 1)] : null;
    return { runs: runs.length, toolCalls: runs.reduce((sum, run) => sum + (run.events || []).filter((event) => ['function_tool', 'built_in_tool'].includes(event.stage)).length, 0),
      tokens: runs.reduce((s, r) => s + (r.input_tokens || 0) + (r.output_tokens || 0), 0),
      missingUsage: runs.filter((r) => !r.usage_complete).length,
      failed: runs.filter(needsAttention).length,
      p50: percentile(0.5), p95: percentile(0.95) };
  }, [history.data]);
  const busy = starting || (jobId && (!job || ['queued', 'running'].includes(job.status)));
  const canRag = selected.length > 0 && selected.every((id) => models.find((m) => m.id === id)?.file_search);
  async function start(event) {
    event.preventDefault(); setError(null); setStarting(true);
    try {
      const result = await api.startChatEvaluation({ message: prompt, model_profiles: selected,
        use_file_search: rag && canRag, expected_terms: terms.split(',').map((s) => s.trim()).filter(Boolean),
        ...(selected.includes(regionalId) ? { regional_model: {
          label: regional.label, region: regional.region, model_id: regional.model_id, api_mode: regional.api_mode,
          ...(regional.api_mode === 'native_chat' ? { compartment_id: regional.compartment_id, max_completion_tokens: regional.max_completion_tokens }
            : { project_id: regional.project_id, auth_mode: regional.auth_mode, ...(regional.auth_mode === 'genai_api_key' ? { api_key: regional.api_key } : {}), vector_store_ids: regional.vector_store_ids.split(',').map((s) => s.trim()).filter(Boolean) })
        } } : {}) });
      setJob({ status: result.status, results: [] }); setJobId(result.run_id);
      sessionStorage.setItem(storageKey, result.run_id);
    } catch (e) { setError(e); } finally { setStarting(false); setRegional((value) => ({ ...value, api_key: '' })); }
  }
  async function rate(runId, rating) {
    if (!rating) return;
    setRatingBusy(runId); setError(null);
    try { await api.rateChatRun(runId, Number(rating)); history.reload(); }
    catch (e) { setError(e); } finally { setRatingBusy(null); }
  }
  return <section className="chat-evaluation evaluation-dashboard" aria-label="Model evaluation workspace">
    <div className="evaluation-tabbar" role="tablist" aria-label="Evaluation views">
      {tabs.map(([id, title], index) => <button key={id} id={`eval-tab-${id}`} role="tab" aria-selected={tab === id} aria-controls={`eval-view-${id}`} tabIndex={tab === id ? 0 : -1}
        onClick={() => setTab(id)} onKeyDown={(event) => {
          const next = event.key === 'ArrowRight' ? (index + 1) % tabs.length : event.key === 'ArrowLeft' ? (index + tabs.length - 1) % tabs.length : event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : null;
          if (next !== null) { event.preventDefault(); setTab(tabs[next][0]); document.getElementById(`eval-tab-${tabs[next][0]}`)?.focus(); }
        }}>{title}</button>)}
      <span className="evaluation-state" role="status">{busy ? 'Comparison running' : `${summary.runs} recorded runs`}</span>
    </div>
    <div className="evaluation-body">
    <ErrorNotice error={history.error} retry={history.reload} />
    <div id="eval-view-overview" role="tabpanel" aria-labelledby="eval-tab-overview" hidden={tab !== 'overview'}>
    <div className="evaluation-subheading"><div><h3>Session performance</h3><p>Measured usage and execution outcomes across your recorded runs.</p></div><button onClick={history.reload}>Refresh metrics</button></div>
    <div className="evaluation-section-label"><span>SESSION OVERVIEW</span><span>OCI Responses <span aria-hidden="true">/</span> Performance</span></div>
    <div className="session-metrics-grid" aria-label="Session performance metrics">
      <article><span>Reported tokens</span><strong>{count(summary.tokens)}</strong><small>{summary.missingUsage} runs missing complete usage</small></article>
      <article><span>Recorded tool calls</span><strong>{count(summary.toolCalls)}</strong><small>Function and built-in tool execution events</small></article>
      <article><span>Latency · p50 / p95</span><strong>{duration(summary.p50)} / {duration(summary.p95)}</strong><small>End-to-end duration of recorded runs</small></article>
      <article><span>Runs requiring attention</span><strong>{summary.failed} / {summary.runs}</strong><small>Failed, blocked, unavailable, or limit reached</small></article>
    </div>
    <p className="evaluation-note">{history.data?.retention || 'Metrics are scoped to this session'}. Tokens are provider-reported; timings are measured by the application.</p>
    <EstimatedFinOps records={records} models={models} />
    <div className="overview-actions"><button onClick={() => setTab('compare')}>Compare models →</button><button onClick={() => setTab('traces')}>Inspect execution traces →</button></div>
    </div>
    <div role="tabpanel" id="eval-view-compare" aria-labelledby="eval-tab-compare" hidden={tab !== 'compare'}>
    <div className="comparison-workspace" id="model-comparison">
      {job && <div className="comparison-status-row"><span className="evaluation-status" role="status">{job.status}</span></div>}
      {models.length < 2 && <p className="evaluation-notice">Add at least two models to enable comparison.</p>}
      {!canEvaluate && <p className="evaluation-notice">Connect an external model or configure OCI models to enable comparison.</p>}
      <form onSubmit={start}>
        <fieldset disabled={Boolean(busy)} aria-label="Models to compare (select two or three)">
        <div className="comparison-model-actions"><select aria-label="Add comparison model" value="" onChange={(event) => { if (event.target.value === 'external') setExternalDialog(true); }}>
          <option value="">Add model…</option><option value="external">External model…</option>
        </select></div><div className="model-choices">
          {models.map((m) => <label key={m.id} title={m.model_id}><input type="checkbox" aria-label={m.label} checked={selected.includes(m.id)}
            disabled={!selected.includes(m.id) && selected.length >= 3}
            onChange={(e) => setSelected((s) => e.target.checked ? [...s, m.id] : s.filter((id) => id !== m.id))} />
            <span><b>{m.label}</b></span></label>)}
        </div>
          <RegionalModelForm enabled={regionalEnabled} value={regional} onChange={setRegional} selected={selected.includes(regionalId)} available={catalog?.regional_evaluation_enabled !== false} toggle={(enabled) => {
            setRegionalEnabled(enabled); setRegional((value) => ({ ...value, api_key: '' }));
            setSelected((values) => enabled ? [...values.slice(0, 2), regionalId] : values.filter((id) => id !== regionalId));
          }} />
          <details className="comparison-contract"><summary>Same prompt · Fresh context · Read-only tools</summary><p>Each model chooses its own tool calls. Compare completed answers and execution evidence before adopting a replacement.</p></details>
          <div className="evaluation-scenarios"><span>Prompt starters</span><button type="button" onClick={() => setPrompt('Which items are at risk of a stockout, and what should I review next?')}>Inventory overview</button><button type="button" onClick={() => setPrompt('Using current inventory and available supplier data, identify up to three items with the most urgent stockout risk. For each, compare stock coverage with supplier lead time, quantify any gap using returned data, and recommend a priority and next action. Explain trade-offs and identify missing information rather than inventing values. Return a comparison table followed by a concise recommendation. Keep recommendations advisory and read-only.')}>Multi-constraint analysis</button></div>
          <label className="eval-field">Evaluation prompt<textarea rows="3" required maxLength="4000" value={prompt} onChange={(e) => setPrompt(e.target.value)} /></label>
          <div className="evaluation-options"><label className="eval-field">Expected terms (optional, comma separated)<input value={terms} maxLength="1000" onChange={(e) => setTerms(e.target.value)} placeholder="e.g. stockout, coverage, review" /></label>
            <label className="eval-rag" title={!canRag ? 'Requires Responses-compatible models and configured vector stores for every selected model.' : undefined}><input type="checkbox" disabled={!canRag} checked={rag && canRag} onChange={(e) => setRag(e.target.checked)} /> Built-in File Search / RAG</label></div>
        </fieldset>
        <div className="evaluation-submit"><button className="primary" disabled={Boolean(busy) || selected.length < 2 || !canEvaluate}>{busy && <span className="evaluation-spinner" aria-hidden="true" />}{busy ? 'Running comparison…' : 'Run comparison'}{!busy && <span aria-hidden="true">→</span>}</button><span>Fresh context for each model · Model calls may incur charges</span></div>
      </form>
      {externalDialog && <ExternalModelDialog purpose="comparison" onClose={() => setExternalDialog(false)} onConnected={async (profile) => {
        setConnectedModels((current) => [...current.filter((model) => model.id !== profile.id), profile]);
        setSelected((current) => [...current.filter((id) => id !== profile.id).slice(0, 2), profile.id]);
        onModelsChanged?.();
        return true;
      }} />}
      <ErrorNotice error={error} retry={() => { setError(null); setPollRevision((n) => n + 1); history.reload(); }} />
      {job?.message && <details className="evaluation-run-context"><summary>Run details{job.regional_model && ` · Regional API: ${job.regional_model.api_mode === 'native_chat' ? 'Native OCI Chat' : 'Responses'}`}</summary><p>{job.message}</p><small>File Search {job.use_file_search ? 'enabled' : 'disabled'}</small></details>}
      {job?.results?.length > 0 && <div className="comparison-scorecard"><h3>Results</h3><div className="events-table"><table><thead><tr><th>Model</th><th>Outcome</th><th>Latency</th><th>Input / output</th><th>Tool calls</th><th>Est. model cost</th><th>Your rating</th></tr></thead><tbody>{job.results.map((result, index) => {
        const metrics = byId[result.metrics?.run_id]?.metrics || result.metrics;
        const profile = metrics?.profile_id || result.profile_id || job.model_profiles?.[index];
        return <tr key={profile || index}><th scope="row">{profile === regionalId ? job.regional_model?.label || regional.label : models.find((model) => model.id === profile)?.label || profile}</th><td>{outcome(metrics)?.replaceAll('_', ' ') || result.mode || 'Not reported'}</td><td>{duration(metrics?.duration_ms)}</td><td>{count(metrics?.input_tokens)} / {count(metrics?.output_tokens)}</td><td>{metrics ? (metrics.events || []).filter((event) => ['function_tool', 'built_in_tool'].includes(event.stage)).length : 'Not reported'}</td><td>{money(modelCost(metrics))}</td><td>{metrics?.quality?.human_rating ? `${metrics.quality.human_rating} / 5` : 'Not rated'}</td></tr>;
      })}</tbody></table></div></div>}
      <div className="comparison-results">{job?.results?.map((result, i) => {
        const m = byId[result.metrics?.run_id]?.metrics || result.metrics;
        const profileId = m?.profile_id || result.profile_id || job.model_profiles?.[i];
        const name = profileId === regionalId ? job.regional_model?.label || regional.label : models.find((p) => p.id === profileId)?.label || profileId;
        return <article className="comparison-card" key={profileId || i}><header><span className="eyebrow">MODEL {i + 1}</span><h3>{name}</h3></header>
          <Markdown className="comparison-answer">{result.message}</Markdown><RunMetrics metrics={m} />
          {m?.quality && <div className="quality-checks"><b>Quality checks</b>
            <p>Tool success: {m.quality.tools_succeeded == null ? 'No tools called' : m.quality.tools_succeeded ? 'Passed' : 'Failed'} · Citations: {m.citations?.length || 0}</p>
            {m.quality.expected_terms?.length > 0 && <ul>{m.quality.expected_terms.map((t) => <li key={t.term}>{t.found ? '✓' : '—'} {t.term}</li>)}</ul>}
            <label>Reviewer rating<select aria-label={`Rate ${name}`} disabled={ratingBusy === m.run_id} value={m.quality.human_rating || ''} onChange={(e) => rate(m.run_id, e.target.value)}>
              <option value="">Not rated</option><option value="1">1 · Incorrect / unusable</option><option value="2">2 · Major corrections</option><option value="3">3 · Useful with corrections</option><option value="4">4 · Accurate and useful</option><option value="5">5 · Accurate, complete, clear</option>
            </select></label></div>}
        </article>;
      })}</div>
      {job?.status === 'interrupted' && <p className="evaluation-notice">The server restarted during this comparison. Completed results are retained; start a new comparison to try again.</p>}
    </div>
    </div>
    <div role="tabpanel" id="eval-view-traces" aria-labelledby="eval-tab-traces" hidden={tab !== 'traces'}>
      <TraceExplorer records={records} models={models} loading={history.loading} refresh={history.reload} />
    </div>
    <div role="tabpanel" id="eval-view-cloud" aria-labelledby="eval-tab-cloud" hidden={tab !== 'cloud'}>
      <CloudOperations active={active && tab === 'cloud'} records={records} models={models} />
    </div>
    </div>
  </section>;
}
