import { useState } from 'react';
import api from '../api';
import { Empty, ErrorNotice, Loading, Panel, Status, number, useResource } from '../ui';

export default function RequestActivity({ session, revision, navigate }) {
  const [offset, setOffset] = useState(0);
  const resource = useResource(() => api.cases(offset), [offset, revision], 15000);
  return (
    <Panel title="Requests and responses" subtitle="Saved outcomes from your agent conversations. Updates every 15 seconds."
      action={<button disabled={resource.loading} onClick={resource.reload}>Refresh</button>}>
      <ErrorNotice error={resource.error} retry={resource.reload} />
      {resource.loading ? <Loading /> : resource.data && (
        resource.data.items.length ? <>
          <div className="table-scroll"><table>
            <thead><tr><th>Request</th><th>Item / quantity</th><th>Supplier</th><th>Progress</th><th>Delivery</th><th><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>{resource.data.items.map((c) => <tr key={c.case_id}>
              <td><strong>#{c.case_id.slice(0, 8)}</strong></td>
              <td>{c.item_id}<small>{number(c.recommended_quantity)} units</small></td>
              <td>{c.selected_supplier_id || 'Not selected'}</td>
              <td><Status value={c.status} /></td>
              <td><small>Request: {c.request_notification_status || 'Not sent'}</small><small>Response: {c.response_notification_status || 'Not sent'}</small></td>
              <td><button onClick={() => navigate('assistant', { case_id: c.case_id })}>Open in chat</button></td>
            </tr>)}</tbody>
          </table></div>
          <div className="activity-pagination">
            <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 20))}>Previous</button>
            <span>Page {Math.floor(offset / 20) + 1}</span>
            <button disabled={!resource.data.has_more || offset >= 10000} onClick={() => setOffset(offset + 20)}>Next</button>
          </div>
        </> : <Empty title="No requests yet">{session.role === 'supplier' ? 'Requests assigned to your supplier account will appear here.' : 'Start with your retail agent in Agent Chat.'}</Empty>
      )}
    </Panel>
  );
}
