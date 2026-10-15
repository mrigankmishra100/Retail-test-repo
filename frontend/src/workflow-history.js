// Browser-only, read-only snapshots of successful business responses. Never sent
// to telemetry or used as approval evidence; current controls reload server state.
const pick = (value, keys) => value && Object.fromEntries(keys.filter((key) => value[key] !== undefined).map((key) => [key, value[key]]));
const quote = (value) => pick(value, ['supplier_id', 'supplier_name', 'item_id', 'quantity', 'unit', 'priced_total', 'unit_price', 'available_quantity', 'feasible', 'lead_time_days', 'payment_terms', 'delivery_terms', 'exclusions', 'warnings', 'infeasibility_reasons', 'policy_source']);
const email = (value) => pick(value, ['subject', 'body']);
export function workflowSnapshot(path, data) {
  if (!data || path === '/chat') return null;
  const c = data.case || (data.case_id ? data : null);
  const record = pick(c, ['case_id', 'version', 'item_id', 'recommended_quantity', 'selected_supplier_id', 'status']);
  if (record) record.quote = quote(c.draft?.quote);
  let title = path.includes('/workflows') ? 'Assessment returned' : 'Request updated';
  if (path.endsWith('/selection')) title = 'Supplier selected';
  if (path.endsWith('/manager-decision')) title = c?.status === 'REQUEST_QUEUED' ? 'Manager approval recorded' : c?.status === 'MANAGER_REJECTED' ? 'Manager rejection recorded' : 'Manager decision returned';
  if (path.endsWith('/supplier-decision')) title = c?.status === 'RESPONSE_QUEUED' ? 'Supplier approval recorded' : c?.status === 'SUPPLIER_REJECTED' ? 'Supplier rejection recorded' : 'Supplier decision returned';
  if (path.endsWith('/supplier-email')) title = 'Draft preview returned';
  if (path.includes('/dispatch/')) title = 'Notification result';
  if (path.endsWith('/complete')) title = 'Completion result';
  if (path.endsWith('/model')) title = 'Workflow model updated';
  const comparison = data.comparison && { item_id: data.comparison.item_id, quantity: data.comparison.quantity,
    suppliers: (data.comparison.suppliers || []).map(quote), recommended_supplier_id: data.comparison.recommended_supplier_id };
  // Copy now so later case refreshes cannot rewrite what this turn returned.
  return JSON.parse(JSON.stringify({ title, message: data.message, record, comparison,
    email: email(data.email || (path.includes('supplier-decision') || path.startsWith('/supplier/') ? c?.draft?.supplier_response : c?.draft?.supplier_email)),
    delivery_status: data.delivery_status }));
}
