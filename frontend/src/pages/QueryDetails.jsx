import { Icon } from '../ui';
import './execution-details.css';

export default function QueryDetails({ queries = [] }) {
  if (!queries.length) return null;
  return <details className="query-evidence"><summary><Icon name="file" size={15} /> View executed {queries.length === 1 ? 'query' : `queries (${queries.length})`}<span>Read only</span></summary>
    <p className="execution-detail-note">The model selected a constrained query plan. The server executed this fixed SQL template with separate bind values.</p>
    {queries.map((query, index) => <section key={index} aria-label={`Executed query ${index + 1}`}>
      <div className="query-evidence-heading"><strong>{query.operation.replace('nl2sql_', '').replaceAll('_', ' ')}</strong><span>{query.row_count} rows returned</span></div>
      <pre tabIndex={0} aria-label="Executed SQL template"><code>{query.sql}</code></pre>
      <div className="query-bindings"><table><caption>Bind parameters</caption><thead><tr><th>Parameter</th><th>Value</th></tr></thead>
        <tbody>{Object.entries(query.parameters || {}).map(([name, value]) => <tr key={name}><td><code>:{name}</code></td><td><code>{JSON.stringify(value)}</code></td></tr>)}</tbody>
      </table></div>
    </section>)}
  </details>;
}
