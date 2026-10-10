import { useState } from 'react';
import api from '../api';
import {
  Badge,
  Dialog,
  Empty,
  ErrorNotice,
  Icon,
  Loading,
  Panel,
  Status,
  human,
  money,
  number,
  useAction,
  useResource,
} from '../ui';

export function Overview({ session, navigate, revision }) {
  const retail = session.role === 'retail-manager';
  const risks = useResource(
    () => (retail ? api.risks() : Promise.resolve(null)),
    [retail, revision],
  );
  const cases = useResource(() => api.cases(), [revision], 15000);
  const report = risks.data;
  const pending = cases.data?.items.filter(
    (c) => c.status === (retail ? 'AWAITING_MANAGER_APPROVAL' : 'AWAITING_SUPPLIER_APPROVAL'),
  );
  return (
    <>
      <div className="overview-banner">
        <div className="banner-icon">
          <Icon name="pulse" size={28} />
        </div>
        <div>
          <h2>
            {retail
              ? 'Every shelf has a story. Stay one step ahead.'
              : 'Better coordination. Confident commitments.'}
          </h2>
          <p>
            {retail
              ? 'Monitor stock coverage, review recommendations, and keep replenishment moving.'
              : 'Review incoming requests and respond with verified inventory and policy terms.'}
          </p>
        </div>
        <button className="overview-bot" onClick={() => navigate('assistant')} aria-label="Open Agent Chat">
          <span className="bot-orbit"><Icon name="bot" size={30} /></span>
          <span>Talk to your agent<small>Let’s work through it together</small></span>
          <Icon name="arrow" size={18} />
        </button>
      </div>
      <ErrorNotice error={risks.error} retry={risks.reload} />
      <div className="metrics">
        {(retail
          ? [
              [
                'Items monitored',
                report?.summary.inventory_item_count,
                'Across your inventory',
                'box',
                '',
              ],
              [
                'At-risk items',
                report?.summary.at_risk_count,
                'Require replenishment review',
                'alert',
                'red',
              ],
              [
                'Healthy stock',
                report?.summary.not_at_risk_count,
                'Within the risk horizon',
                'shield',
                'green',
              ],
              [
                'Needs assessment',
                report?.summary.missing_history_count,
                'Insufficient sales history',
                'pulse',
                'ochre',
              ],
            ]
          : [
              [
                'Requests on this page',
                cases.data?.items.length,
                'Supplier-scoped cases',
                'file',
                '',
              ],
              ['Awaiting your review', pending?.length, 'On the current page', 'shield', 'ochre'],
              [
                'Completed on this page',
                cases.data?.items.filter((c) => c.status === 'COMPLETED').length,
                'Approved workflow outcomes',
                'check',
                'green',
              ],
            ]
        ).map(([label, value, detail, icon, color]) => (
          <section className={`metric ${color}`} key={label}>
            <div>
              <span>{label}</span>
              <Icon name={icon} />
            </div>
            <strong>{number(value)}</strong>
            <small>{detail}</small>
          </section>
        ))}
      </div>
      <div className="overview-grid overview-primary">
        <Panel
          title={retail ? 'Inventory requiring attention' : 'Incoming requests'}
          subtitle={
            retail
              ? `Projected stock over ${report?.rules.risk_horizon_days ?? 'the configured'} days`
              : 'Requests visible to your supplier session'
          }
          action={
            <button
              className="text-button"
              onClick={() => navigate(retail ? 'assistant' : 'activity')}
            >
              {retail ? 'Review in chat' : 'View activity'} <Icon name="arrow" size={16} />
            </button>
          }
        >
          {retail ? (
            risks.loading ? (
              <Loading />
            ) : report ? (
              <InventoryTable
                items={report.at_risk_items.slice(0, 5)}
                navigate={navigate}
                compact
              />
            ) : (
              <Empty title="Inventory is unavailable">
                Connect to the backend and retry to see current stock.
              </Empty>
            )
          ) : (
            <CasePreview resource={cases} navigate={navigate} />
          )}
        </Panel>
      </div>
      <div className="overview-grid lower-grid">
        <Panel
          title="Needs your review"
          subtitle="Open a request in Agent Chat to review and decide"
          action={
            <button className="text-button" onClick={() => navigate('activity')}>
              View activity <Icon name="arrow" size={16} />
            </button>
          }
        >
          <ErrorNotice error={cases.error} retry={cases.reload} />
          {cases.loading ? (
            <Loading />
          ) : pending?.length ? (
            pending.slice(0, 3).map((c) => (
              <div className="activity-row" key={c.case_id}>
                <span className="activity-icon">
                  <Icon name="shield" />
                </span>
                <div>
                  <strong>
                    {c.item_id} · {number(c.recommended_quantity)} units
                  </strong>
                  <small>
                    Request {c.case_id.slice(0, 8)} ·{' '}
                    {c.selected_supplier_id || 'Supplier not selected'}
                  </small>
                </div>
                <button onClick={() => navigate('assistant', { case_id: c.case_id })}>Review in chat</button>
              </div>
            ))
          ) : (
            !cases.error && (
              <Empty title="You're all caught up" icon="check">
                No requests need your decision on this page.
              </Empty>
            )
          )}
        </Panel>
        <Panel title="Your replenishment process" subtitle="A connected workflow with human review">
          <ol className="process-list">
            {[
              ['Assess inventory', 'Use sales history to identify stock risk.'],
              ['Compare & select', 'Evaluate availability, pricing, and policy.'],
              ['Review & approve', 'Confirm the exact commercial draft.'],
              ['Track the outcome', 'Follow delivery and supplier responses.'],
            ].map(([title, description], i) => (
              <li key={title}>
                <span>{String(i + 1).padStart(2, '0')}</span>
                <div>
                  <strong>{title}</strong>
                  <small>{description}</small>
                </div>
              </li>
            ))}
          </ol>
        </Panel>
      </div>
    </>
  );
}
function CasePreview({ resource, navigate }) {
  return (
    <>
      <ErrorNotice error={resource.error} retry={resource.reload} />
      {resource.loading ? (
        <Loading />
      ) : resource.data?.items.length ? (
        resource.data.items.slice(0, 5).map((c) => (
          <div className="activity-row" key={c.case_id}>
            <Icon name="file" />
            <div>
              <strong>{c.item_id}</strong>
              <small>
                {number(c.recommended_quantity)} units · {c.case_id.slice(0, 8)}
              </small>
            </div>
            <Status value={c.status} />
            <button onClick={() => navigate('assistant', { case_id: c.case_id })}>Open in chat</button>
          </div>
        ))
      ) : (
        !resource.error && (
          <Empty title="No requests yet">
            Incoming requests will appear here when the backend makes them available.
          </Empty>
        )
      )}
    </>
  );
}

export function InventoryTable({ items, navigate, onDetail, compact = false }) {
  if (!items.length)
    return (
      <Empty title="No items in this view" icon="check">
        Adjust the filters or refresh the inventory assessment.
      </Empty>
    );
  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th>Item</th>
            <th>On hand</th>
            <th>Coverage</th>
            {!compact && <th>Projected stock</th>}
            <th>Status</th>
            <th>
              <span className="sr-only">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.item_id}>
              <td>
                <div className="item-cell">
                  <span className="product-icon">
                    <Icon name="box" size={18} />
                  </span>
                  <div>
                    <strong>{item.item_name}</strong>
                    <small>
                      {item.item_id}
                      {item.category ? ` · ${item.category}` : ''}
                    </small>
                  </div>
                </div>
              </td>
              <td className="numeric">
                {number(item.current_stock)}
                <small>units</small>
              </td>
              <td>
                <strong>
                  {item.status === 'missing_history'
                    ? '—'
                    : item.days_of_supply == null
                      ? 'No demand'
                      : `${number(item.days_of_supply)} days`}
                </strong>
                {item.days_of_supply != null && (
                  <span className="coverage-track">
                    <span
                      style={{
                        width: `${Math.min(100, Math.max(0, (item.days_of_supply / (item.target_coverage_days || 14)) * 100))}%`,
                      }}
                      className={item.status === 'at_risk' ? 'low' : ''}
                    />
                  </span>
                )}
              </td>
              {!compact && <td className="numeric">{number(item.projected_stock)}</td>}
              <td>
                <Status value={item.status} />
                {item.history_quality?.recommendation_is_provisional && (
                  <small className="quality-warning">Provisional estimate</small>
                )}
              </td>
              <td>
                {onDetail ? (
                  <button className="text-button" onClick={() => onDetail(item)}>
                    Details <Icon name="chevron" size={14} />
                  </button>
                ) : (
                  <button className="text-button" onClick={() => navigate('workflows', item)}>
                    Review <Icon name="chevron" size={14} />
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
export function Inventory({ navigate, revision }) {
  const [horizon, setHorizon] = useState(''),
    [filter, setFilter] = useState('all'),
    [search, setSearch] = useState(''),
    [selected, setSelected] = useState(null);
  const resource = useResource(() => api.risks(horizon || undefined), [horizon, revision]);
  const all = resource.data
    ? [
        ...resource.data.at_risk_items,
        ...resource.data.not_at_risk_items,
        ...resource.data.unassessed_items,
      ]
    : [];
  const items = all.filter(
    (i) =>
      (filter === 'all' || i.status === filter) &&
      `${i.item_name} ${i.item_id} ${i.category || ''}`
        .toLowerCase()
        .includes(search.toLowerCase()),
  );
  return (
    <>
      <Panel
        title="Inventory risk report"
        subtitle="Calculated from current Oracle inventory and sales history"
        action={
          <button onClick={resource.reload} disabled={resource.loading}>
            <Icon name="refresh" size={16} /> Refresh
          </button>
        }
      >
        <div className="toolbar">
          <label className="search-field">
            <Icon name="search" size={18} />
            <input
              aria-label="Search inventory"
              placeholder="Search by item name or ID"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </label>
          <label className="inline-label">
            Risk horizon
            <select value={horizon} onChange={(e) => setHorizon(e.target.value)}>
              <option value="">Configured default</option>
              <option value="7">7 days</option>
              <option value="14">14 days</option>
              <option value="30">30 days</option>
            </select>
          </label>
        </div>
        <div className="tabs" aria-label="Inventory filter">
          {[
            ['all', 'All items'],
            ['at_risk', 'At risk'],
            ['not_at_risk', 'Healthy'],
            ['missing_history', 'Needs assessment'],
          ].map(([id, label]) => (
            <button
              key={id}
              aria-pressed={filter === id}
              className={filter === id ? 'active' : ''}
              onClick={() => setFilter(id)}
            >
              {label}
              <span>{all.filter((i) => id === 'all' || i.status === id).length}</span>
            </button>
          ))}
        </div>
        <ErrorNotice error={resource.error} retry={resource.reload} />
        {resource.loading ? (
          <Loading />
        ) : (
          !resource.error && (
            <InventoryTable items={items} navigate={navigate} onDetail={setSelected} />
          )
        )}
        <div className="table-footer">
          {items.length} items shown{' '}
          <span>
            Demand window: {resource.data?.rules.demand_window_days ?? '—'} days · Target coverage:{' '}
            {resource.data?.rules.target_coverage_days ?? '—'} days
          </span>
        </div>
      </Panel>
      {selected && (
        <Dialog title={selected.item_name} onClose={() => setSelected(null)}>
          <InventoryDetail item={selected} navigate={navigate} close={() => setSelected(null)} />
        </Dialog>
      )}
    </>
  );
}

const forecastDateFormatter = new Intl.DateTimeFormat('en-IN', {
  day: 'numeric',
  month: 'short',
  year: 'numeric',
});

function addDays(dateText, days) {
  const [year, month, day] = dateText.split('-').map(Number);
  return new Date(year, month - 1, day + days);
}

function baselineForecast(item) {
  const currentStock = Number(item.current_stock);
  const dailyDemand = Number(item.average_daily_demand);
  const safetyStock = Number(item.safety_stock);
  const asOfDate = item.history_quality?.as_of_date;
  if (![currentStock, dailyDemand, safetyStock].every(Number.isFinite) || dailyDemand <= 0 || !asOfDate) {
    return null;
  }

  const horizon = Math.max(1, Number(item.risk_horizon_days) || 7);
  const safetyDay = Math.max(0, Math.ceil((currentStock - safetyStock) / dailyDemand));
  const stockoutDay = Math.max(0, Math.ceil(currentStock / dailyDemand));
  return {
    currentStock,
    dailyDemand,
    safetyStock,
    horizon,
    safetyDay,
    stockoutDay,
    dateFor: (days) => forecastDateFormatter.format(addDays(asOfDate, days)),
    points: Array.from({ length: horizon + 1 }, (_, day) => ({
      day,
      stock: Math.max(0, currentStock - dailyDemand * day),
    })),
  };
}

function StockForecast({ item }) {
  const forecast = baselineForecast(item);
  if (!forecast) return null;

  const width = 620;
  const height = 210;
  const padding = { top: 18, right: 18, bottom: 34, left: 44 };
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;
  const topStock = Math.max(forecast.currentStock, forecast.safetyStock, 1);
  const x = (day) => padding.left + (day / forecast.horizon) * plotWidth;
  const y = (stock) => padding.top + ((topStock - stock) / topStock) * plotHeight;
  const line = forecast.points.map((point) => `${x(point.day)},${y(point.stock)}`).join(' ');

  return (
    <section className="stock-forecast" aria-labelledby="stock-forecast-title">
      <div className="forecast-heading">
        <div>
          <h3 id="stock-forecast-title">Stock projection</h3>
          <p>{forecast.horizon}-day view using verified average daily demand.</p>
        </div>
        <Badge>Baseline forecast</Badge>
      </div>
      <div className="forecast-summary">
        <div>
          <span>Safety-stock risk</span>
          <strong>{forecast.dateFor(forecast.safetyDay)}</strong>
          <small>{forecast.safetyDay === 0 ? 'Already at risk' : `In ${forecast.safetyDay} days`}</small>
        </div>
        <div>
          <span>Predicted stockout</span>
          <strong>{forecast.dateFor(forecast.stockoutDay)}</strong>
          <small>{forecast.stockoutDay === 0 ? 'Stock depleted' : `In ${forecast.stockoutDay} days`}</small>
        </div>
        <div>
          <span>Demand rate</span>
          <strong>{number(forecast.dailyDemand)} units/day</strong>
          <small>From recorded sales</small>
        </div>
      </div>
      <div className="forecast-chart">
        <svg
          viewBox={`0 0 ${width} ${height}`}
          role="img"
          aria-label={`Projected inventory for ${item.item_name} over ${forecast.horizon} days`}
        >
          <line
            className="forecast-axis"
            x1={padding.left}
            y1={y(0)}
            x2={width - padding.right}
            y2={y(0)}
          />
          <line
            className="forecast-safety-line"
            x1={padding.left}
            y1={y(forecast.safetyStock)}
            x2={width - padding.right}
            y2={y(forecast.safetyStock)}
          />
          <polyline className="forecast-stock-line" points={line} />
          <circle
            className="forecast-current-point"
            cx={x(0)}
            cy={y(forecast.currentStock)}
            r="4"
          />
          {forecast.stockoutDay <= forecast.horizon && (
            <circle
              className="forecast-stockout-point"
              cx={x(forecast.stockoutDay)}
              cy={y(0)}
              r="5"
            />
          )}
          <text x="4" y={y(topStock) + 4}>{number(topStock)}</text>
          <text x="24" y={y(0) + 4}>0</text>
          <text x={padding.left} y={height - 8}>{forecast.dateFor(0)}</text>
          <text x={width - padding.right} y={height - 8} textAnchor="end">
            {forecast.dateFor(forecast.horizon)}
          </text>
        </svg>
      </div>
      <div className="forecast-legend">
        <span><i className="forecast-key stock" />Projected on hand</span>
        <span><i className="forecast-key safety" />Safety stock</span>
      </div>
      <p className="forecast-note">
        This is a transparent baseline forecast, not an AI-generated order quantity.
      </p>
    </section>
  );
}

function InventoryCalculation({ item }) {
  const steps = [
    {
      title: 'Daily demand',
      formula: `${number(item.total_demand)} total sales / ${number(item.observed_days)} observed days`,
      result: `${number(item.average_daily_demand)} units/day`,
    },
    {
      title: 'Projected stock',
      formula: `${number(item.current_stock)} current - (${number(item.average_daily_demand)}/day x ${number(item.risk_horizon_days)} days)`,
      result: `${number(item.projected_stock)} units`,
    },
    {
      title: 'Target stock',
      formula: `(${number(item.average_daily_demand)}/day x ${number(item.target_coverage_days)} days) + ${number(item.safety_stock)} safety`,
      result: `${number(item.target_stock)} units`,
    },
    {
      title: 'Recommended quantity',
      formula: `ceil(max(0, ${number(item.target_stock)} target - ${number(item.current_stock)} current))`,
      result: `${number(item.recommended_quantity)} units`,
    },
  ];
  const atRisk = item.status === 'at_risk';

  return (
    <section className="calculation-card" aria-labelledby="calculation-title">
      <div className="calculation-heading">
        <div>
          <h3 id="calculation-title">How the recommendation was calculated</h3>
          <p>Each result comes from the inventory and recorded sales shown below.</p>
        </div>
        <Badge>Verified inputs</Badge>
      </div>
      <ol className="calculation-steps">
        {steps.map((step, index) => (
          <li key={step.title}>
            <span className="calculation-number">{index + 1}</span>
            <div>
              <strong>{step.title}</strong>
              <code>{step.formula}</code>
            </div>
            <b>{step.result}</b>
          </li>
        ))}
      </ol>
      <div className={`calculation-decision ${atRisk ? 'risk' : 'healthy'}`}>
        <Icon name={atRisk ? 'alert' : 'shield'} size={18} />
        <div>
          <small>Risk check</small>
          <strong>{atRisk ? 'Replenishment review recommended' : 'Stock is within the safety threshold'}</strong>
          <span>
            Projected stock {number(item.projected_stock)}{' '}
            {atRisk ? 'reaches or falls below' : 'remains above'} safety stock{' '}
            {number(item.safety_stock)}.
          </span>
        </div>
      </div>
    </section>
  );
}

function InventoryDetail({ item, navigate, close }) {
  const [offset, setOffset] = useState(0);
  const sales = useResource(() => api.sales(item.item_id, offset), [item.item_id, offset]);
  return (
    <>
      <div className="detail-title">
        <Badge>{item.item_id}</Badge>
        <Status value={item.status} />
      </div>
      {item.status === 'missing_history' ? (
        <p>{item.reason}</p>
      ) : (
        <InventoryCalculation item={item} />
      )}
      <dl className="facts">
        <div>
          <dt>Current stock</dt>
          <dd>{number(item.current_stock)} units</dd>
        </div>
        <div>
          <dt>Daily demand</dt>
          <dd>{number(item.average_daily_demand)}</dd>
        </div>
        <div>
          <dt>Reorder point</dt>
          <dd>{number(item.reorder_point)}</dd>
        </div>
        <div>
          <dt>Safety stock</dt>
          <dd>{number(item.safety_stock)}</dd>
        </div>
        <div>
          <dt>Recommended quantity</dt>
          <dd>{number(item.recommended_quantity)}</dd>
        </div>
        <div>
          <dt>Last sale</dt>
          <dd>{item.history_quality?.last_sale_date || '—'}</dd>
        </div>
      </dl>
      <StockForecast item={item} />
      {item.history_quality?.warnings.map((w) => (
        <div className="callout" key={w}>
          {w}
        </div>
      ))}
      {item.status !== 'missing_history' && (
        <button
          className="primary"
          onClick={() => {
            navigate('workflows', item);
            close();
          }}
        >
          Review replenishment <Icon name="arrow" size={16} />
        </button>
      )}
      <h3 className="section-title">Sales history · last 30 days</h3>
      <ErrorNotice error={sales.error} retry={sales.reload} />
      {sales.loading ? (
        <Loading />
      ) : sales.data?.items.length ? (
        <>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Quantity sold</th>
                </tr>
              </thead>
              <tbody>
                {sales.data.items.map((s) => (
                  <tr key={s.sale_id}>
                    <td>{new Date(s.sale_date).toLocaleDateString()}</td>
                    <td>{number(s.quantity_sold)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pagination offset={offset} hasMore={sales.data.has_more} onChange={setOffset} />
        </>
      ) : (
        !sales.error && (
          <Empty title="No recent sales">
            No sales observations were returned for this window.
          </Empty>
        )
      )}
    </>
  );
}
export function Pagination({ offset, hasMore, onChange, disabled }) {
  return (
    <div className="pagination">
      <span>Page {offset / 20 + 1}</span>
      <button disabled={!offset || disabled} onClick={() => onChange(Math.max(0, offset - 20))}>
        Previous
      </button>
      <button
        disabled={!hasMore || disabled || offset >= 10000}
        onClick={() => onChange(offset + 20)}
      >
        Next
      </button>
    </div>
  );
}
function quoteWarnings(quote) {
  return [
    ...(quote.exclusions || []).map((warning) => `Excludes: ${warning}`),
    ...(quote.history_quality?.warnings || []),
    ...(quote.confirmation_required ? ['Additional confirmation required by policy.'] : []),
  ];
}

function sharedQuoteWarnings(quotes) {
  if (!quotes?.length) return [];
  const warningSets = quotes.map((quote) => new Set(quoteWarnings(quote)));
  return [...warningSets[0]].filter((warning) =>
    warningSets.slice(1).every((warnings) => warnings.has(warning)),
  );
}

function ComparisonNotice({ warnings }) {
  if (!warnings.length) return null;
  return (
    <aside className="comparison-notice" aria-label="Terms applying to all supplier options">
      <Icon name="alert" size={20} />
      <div>
        <strong>Review before selecting</strong>
        <span>These conditions apply to every supplier option shown.</span>
        <ul>
          {warnings.map((warning) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      </div>
    </aside>
  );
}

export function QuoteCard({
  quote,
  recommended,
  previouslySelected = false,
  onSelect,
  busy,
  sharedWarnings = [],
}) {
  const cardWarnings = quoteWarnings(quote).filter((warning) => !sharedWarnings.includes(warning));
  return (
    <article className={`quote-card ${recommended ? 'recommended' : ''}`}>
      <div className="quote-heading">
        <span className="supplier-symbol">
          <Icon name="people" />
        </span>
        <div>
          <h3>{quote.supplier_name || quote.supplier_id}</h3>
          <small>{quote.supplier_id}</small>
        </div>
        {(recommended || previouslySelected) && (
          <div className="quote-card-badges">
            {recommended && <Badge tone="success">Recommended</Badge>}
            {previouslySelected && <Badge>Previously selected</Badge>}
          </div>
        )}
      </div>
      <div className="quote-price">
        {quote.priced_total != null ? (
          <>
            {money(quote.priced_total)}
            <small>
              Priced total · {number(quote.quantity)} {quote.unit}
            </small>
          </>
        ) : (
          'Not actionable'
        )}
      </div>
      <div className="quote-action-row">
        <Badge tone={quote.feasible ? 'success' : 'danger'}>
          {quote.feasible ? 'Feasible' : 'Not feasible'}
        </Badge>
        {quote.expedited && quote.surcharge_rate_percent && (
          <Badge tone="warning">Expedite +{quote.surcharge_rate_percent}%</Badge>
        )}
        {onSelect && (
          <button
            className={recommended ? 'primary' : ''}
            disabled={busy || !quote.feasible || quote.priced_total == null}
            onClick={() => onSelect(quote)}
          >
            Select supplier
          </button>
        )}
      </div>
      {quote.priced_total != null && (
        <>
          <dl className="facts quote-facts">
            <div>
              <dt>Unit price</dt>
              <dd>{money(quote.unit_price)}</dd>
            </div>
            <div>
              <dt>Lead time</dt>
              <dd>
                {quote.lead_time_days == null ? 'Unconfirmed' : `${quote.lead_time_days} days`}
              </dd>
            </div>
            <div>
              <dt>Available</dt>
              <dd>{number(quote.available_quantity)}</dd>
            </div>
            <div>
              <dt>Minimum order</dt>
              <dd>{number(quote.minimum_order_quantity)}</dd>
            </div>
            <div>
              <dt>Discount</dt>
              <dd>{money(quote.discount_amount)}</dd>
            </div>
            <div>
              <dt>Surcharge</dt>
              <dd>{money(quote.surcharge)}</dd>
            </div>
          </dl>
          <ul className="quote-terms">
            <li>
              <Icon name="clock" size={16} />
              <div>
                <span>Payment</span>
                <strong>{quote.payment_terms}</strong>
              </div>
            </li>
            <li>
              <Icon name="flow" size={16} />
              <div>
                <span>Delivery</span>
                <strong>{quote.delivery_terms}</strong>
              </div>
            </li>
            <li>
              <Icon name="shield" size={16} />
              <div>
                <span>Special conditions</span>
                <strong>{quote.special_conditions || 'No additional conditions'}</strong>
              </div>
            </li>
            <li>
              <Icon name="file" size={16} />
              <div>
                <span>Policy validity</span>
                <strong>
                  Until {quote.valid_until} · {human(quote.policy_source)} policy
                </strong>
              </div>
            </li>
          </ul>
          {quote.quantity_adjusted && (
            <div className="callout">
              Quantity adjusted from {quote.requested_quantity} to {quote.quantity} to meet supplier
              terms.
            </div>
          )}
        </>
      )}
      {quote.infeasibility_reasons?.map((reason) => (
        <div className="callout" key={reason}>
          {human(reason)}
        </div>
      ))}
      <div className="quote-specific-warnings">
        {cardWarnings.map((warning) => (
          <div key={warning} className="callout">
            {warning}
          </div>
        ))}
      </div>
    </article>
  );
}

export function QuoteComparison({
  quotes,
  recommendedSupplierId,
  previousSupplierId = null,
  onSelect,
  busy,
}) {
  const sharedWarnings = sharedQuoteWarnings(quotes);
  return (
    <>
      <ComparisonNotice warnings={sharedWarnings} />
      <div className="quote-grid">
        {quotes.map((quote) => (
          <QuoteCard
            key={quote.supplier_id}
            quote={quote}
            recommended={quote.supplier_id === recommendedSupplierId}
            previouslySelected={quote.supplier_id === previousSupplierId}
            onSelect={onSelect}
            busy={busy}
            sharedWarnings={sharedWarnings}
          />
        ))}
      </div>
    </>
  );
}
export function Suppliers({ item, navigate }) {
  const [id, setId] = useState(item?.item_id || 'ITEM001'),
    [quantity, setQuantity] = useState(item?.recommended_quantity || 100),
    [expedited, setExpedited] = useState(false),
    [result, setResult] = useState(null);
  const action = useAction();
  async function submit(e) {
    e.preventDefault();
    setResult(null);
    const data = await action.run('compare', () => api.compare(id, Number(quantity), expedited));
    if (data) setResult(data);
  }
  return (
    <>
      <Panel
        title="Find the right supply partner"
        subtitle="Quotes include MOQ, availability, policy terms, and feasibility"
      >
        <form onSubmit={submit} className="form-toolbar">
          <label>
            Item ID
            <input
              required
              pattern="ITEM[0-9]{3}"
              value={id}
              disabled={action.busy}
              onChange={(e) => {
                setId(e.target.value.toUpperCase());
                setResult(null);
              }}
            />
          </label>
          <label>
            Requested units
            <input
              required
              type="number"
              min="1"
              max="1000000"
              value={quantity}
              disabled={action.busy}
              onChange={(e) => {
                setQuantity(e.target.value);
                setResult(null);
              }}
            />
          </label>
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={expedited}
              disabled={action.busy}
              onChange={(e) => {
                setExpedited(e.target.checked);
                setResult(null);
              }}
            />{' '}
            Expedited delivery
          </label>
          <button className="primary" disabled={action.busy}>
            {action.busy ? 'Comparing…' : 'Compare suppliers'}
          </button>
        </form>
        <ErrorNotice error={action.error} />
      </Panel>
      {result ? (
        <>
          <div className="callout info">
            <Icon name="spark" />
            <span>{result.rationale}</span>
          </div>
          <QuoteComparison
            quotes={result.suppliers}
            recommendedSupplierId={result.recommended_supplier_id}
          />
          <div className="callout">
            To select a supplier and create a draft, start the retail workflow with these inputs.
            <button
              onClick={() =>
                navigate('workflows', { item_id: id, recommended_quantity: Number(quantity) })
              }
            >
              Start workflow <Icon name="arrow" size={16} />
            </button>
          </div>
        </>
      ) : (
        !action.busy && (
          <Empty title="Compare with confidence" icon="people">
            Enter an item and quantity to retrieve real supplier quotes.
          </Empty>
        )
      )}
    </>
  );
}
export function SupplierTools() {
  const [id, setId] = useState('ITEM001'),
    [quantity, setQuantity] = useState(100),
    [result, setResult] = useState(null);
  const action = useAction();
  async function submit(e) {
    e.preventDefault();
    setResult(null);
    const data = await action.run('supplier', async () => {
      const [stock, quote] = await Promise.all([
        api.supplierInventory(id),
        api.supplierQuote(id, Number(quantity)),
      ]);
      return { stock, quote };
    });
    if (data) setResult(data);
  }
  return (
    <>
      <Panel
        title="Your stock & quote desk"
        subtitle="Results are isolated to your server-verified supplier identity"
      >
        <form className="form-toolbar" onSubmit={submit}>
          <label>
            Item ID
            <input
              required
              pattern="ITEM[0-9]{3}"
              value={id}
              disabled={action.busy}
              onChange={(e) => {
                setId(e.target.value.toUpperCase());
                setResult(null);
              }}
            />
          </label>
          <label>
            Quantity
            <input
              required
              type="number"
              min="1"
              max="1000000"
              value={quantity}
              disabled={action.busy}
              onChange={(e) => {
                setQuantity(e.target.value);
                setResult(null);
              }}
            />
          </label>
          <button className="primary" disabled={action.busy}>
            {action.busy ? 'Retrieving…' : 'Check stock & quote'}
          </button>
        </form>
        <ErrorNotice error={action.error} />
      </Panel>
      {result ? (
        <div className="supplier-results">
          <div className="metric">
            <div>
              <span>Available inventory</span>
              <Icon name="box" />
            </div>
            <strong>{number(result.stock.available_quantity)}</strong>
            <small>
              {result.stock.item_id} · {result.stock.supplier_id}
            </small>
          </div>
          <QuoteCard quote={result.quote} />
        </div>
      ) : (
        <Empty title="Ready to check availability">
          Enter the item and requested quantity to see your inventory and policy-backed pricing.
        </Empty>
      )}
    </>
  );
}
export function Policy() {
  const resource = useResource(api.policy, []);
  return (
    <Panel
      title="Verified supplier policy"
      subtitle="Document identity and content are validated by the backend"
      action={
        <button onClick={resource.reload}>
          <Icon name="refresh" size={16} /> Refresh
        </button>
      }
    >
      <ErrorNotice error={resource.error} retry={resource.reload} />
      {resource.loading ? (
        <Loading />
      ) : (
        resource.data && (
          <div className="policy-content">
            <div className="detail-title">
              <Badge>{resource.data.supplier_id}</Badge>
              <Badge tone="success">Verified document</Badge>
              <Badge>{human(resource.data.source)}</Badge>
            </div>
            <h3>{resource.data.object_name}</h3>
            {resource.data.fallback_reason && (
              <div className="callout">{resource.data.fallback_reason}</div>
            )}
            <pre>{resource.data.content}</pre>
            <small className="hash">SHA-256: {resource.data.sha256}</small>
          </div>
        )
      )}
    </Panel>
  );
}
export function Platform() {
  const ready = useResource(api.ready, []);
  const readiness = ready.error?.data?.checks ? ready.error.data : ready.data;
  return (
    <>
      <Panel
        title="Dependency readiness"
        subtitle="Bounded backend checks; no model prompts or notifications are sent"
        action={
          <button onClick={ready.reload} disabled={ready.loading}>
            <Icon name="refresh" size={16} /> Check again
          </button>
        }
      >
        <ErrorNotice error={ready.error} retry={ready.reload} />
        {ready.loading ? (
          <Loading />
        ) : (
          readiness && (
            <div className="readiness-grid">
              <Status value={readiness.status} />
              {Object.entries(readiness.checks).map(([name, status]) => (
                <div className="readiness-row" key={name}>
                  <Icon name="pulse" />
                  <strong>{human(name)}</strong>
                  <Badge>{String(status)}</Badge>
                </div>
              ))}
            </div>
          )
        )}
      </Panel>
      <Panel
        title="Built to grow with your operations"
        subtitle="Implementation coverage from this branch; deployment readiness is checked separately"
      >
        <div className="roadmap">
          {[
            [
              '01–06',
              'Core operations',
              'Inventory, supplier policies, cases, sessions, and typed APIs.',
              'Available in backend',
            ],
            [
              '07–08',
              'Retail agent',
              'MCP tools, LangGraph workflow, checkpoints, and retail conversations.',
              'Available in backend',
            ],
            [
              '09',
              'Supplier agent',
              'Guided stock and policy checks, response preview, human approval, and shared case completion.',
              'Connected to workspace',
            ],
            [
              '10',
              'Enterprise AI',
              'Expanded conversation continuity, constrained analytics, and registries.',
              'Further integration',
            ],
            [
              '11',
              'Notifications & observability',
              'Production delivery acceptance and cross-service telemetry.',
              'Deployment checks',
            ],
            [
              '13',
              'Delivery & rollout',
              'End-to-end staging acceptance and production deployment.',
              'Planned',
            ],
          ].map(([phase, title, description, status]) => (
            <article key={phase}>
              <span className="phase-number">{phase}</span>
              <div>
                <h3>{title}</h3>
                <p>{description}</p>
              </div>
              <Badge tone={status === 'Available in backend' ? 'success' : 'neutral'}>
                {status}
              </Badge>
            </article>
          ))}
        </div>
      </Panel>
    </>
  );
}
