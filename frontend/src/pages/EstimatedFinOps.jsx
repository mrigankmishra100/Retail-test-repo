export const money = (value) => typeof value === 'number' && Number.isFinite(value)
  ? `$${value.toLocaleString('en-US', { minimumFractionDigits: 4, maximumFractionDigits: 6 })}` : 'Not estimated';

// Only use the model-only field; legacy totals may include retrieval charges.
export const modelCost = (metrics) => typeof metrics?.model_cost_usd === 'number' && Number.isFinite(metrics.model_cost_usd)
  ? metrics.model_cost_usd : null;

export default function EstimatedFinOps({ records = [], models = [] }) {
  const runs = records.map((r) => r.metrics).filter(Boolean);
  const priced = runs.filter((r) => modelCost(r) !== null);
  const total = priced.reduce((sum, r) => sum + modelCost(r), 0);
  const groups = Object.values(runs.reduce((all, run) => {
    const key = run.model_id || run.profile_id;
    const group = all[key] ||= { id: key, profile: run.profile_id, count: 0, priced: 0, cost: 0, rates: [] };
    group.count++;
    if (modelCost(run) !== null) { group.priced++; group.cost += modelCost(run); }
    if (run.estimated_rates && !group.rates.some((r) => JSON.stringify(r) === JSON.stringify(run.estimated_rates))) group.rates.push(run.estimated_rates);
    return all;
  }, {})).sort((a, b) => b.cost - a.cost);
  return <section className="estimated-finops" aria-label="Estimated model costs">
    <header><div><span className="eyebrow">MODEL USAGE</span><h3>Estimated spend</h3></div><span className="estimate-label">Estimate · USD</span></header>
    <div className="finops-totals"><div><strong>{priced.length ? money(total) : 'Not estimated'}</strong><small>{priced.length} / {runs.length} runs priced</small></div>
      <div><b>{priced.length ? money(total / priced.length) : '—'}</b><small>Average per priced run</small></div></div>
    {groups.length > 0 && <div className="finops-models">{groups.map((group) => <div className="finops-model" key={group.id}>
      <div><span title={group.id}>{models.find((m) => m.id === group.profile)?.label || group.id}</span><b>{group.priced ? money(group.cost) : 'Not estimated'}</b></div>
      <div className="finops-bar" aria-hidden="true"><span style={{ width: `${total > 0 ? group.cost / total * 100 : 0}%` }} /></div>
      <small>{group.count} runs · {group.priced} priced</small>
    </div>)}</div>}
    <p className="finops-scope">Model tokens only · Provider usage × configured rates · Not an OCI bill</p>
    <details><summary>Rates & coverage</summary>
      <p>Excludes File Search, guardrails, storage, database and hosting. Missing usage or rates are excluded, never treated as zero. Earlier runs without a saved estimate remain unpriced.</p>
      <p>Cached input uses its configured rate. Reasoning tokens are already included in output tokens. Rates are saved with each run; changing a rate does not reprice history.</p>
      {groups.map((group) => <div key={group.id}><b>{models.find((m) => m.id === group.profile)?.label || group.id}</b>{group.rates.length ? group.rates.map((rates, i) => <p key={i}>USD / million tokens: input {rates.input_usd_per_million ?? 'unset'} · output {rates.output_usd_per_million ?? 'unset'} · cached input {rates.cached_input_usd_per_million ?? 'unset'} · cache write {rates.cache_write_usd_per_million ?? 'unset'}</p>) : <p>No saved rates.</p>}</div>)}
    </details>
  </section>;
}
