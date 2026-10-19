import { resolveApiBase } from './api-base.js';
import { startActivity, clearActivity } from './activity-client';

const BASE = resolveApiBase(import.meta.url, import.meta.env.VITE_API_BASE_URL).replace(/\/$/, '');
let token = null;
export const setToken = (value) => {
  if (token !== value) clearActivity();
  token = value;
};
export class ApiError extends Error {
  constructor(message, status, traceId) {
    super(message);
    this.status = status;
    this.traceId = traceId;
  }
}
export async function request(path, { method = 'GET', body, key, activityOwnerId } = {}) {
  const requestToken = token;
  const activity = startActivity({ base: BASE, token: requestToken, path, method, activityOwnerId, isCurrentSession: () => token === requestToken });
  let outcome = 'interrupted', activityResponse;
  const controller = new AbortController();
  // Hosted agent calls can take up to 250 seconds; allow the server to return
  // its outcome instead of abandoning a longer multi-tool analysis at 90s.
  const timeout = setTimeout(() => controller.abort(), path === '/chat' ? 270000 : 90000);
  try {
    const response = await fetch(`${BASE}${path}`, {
      method,
      signal: controller.signal,
      headers: {
        ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(key ? { 'Idempotency-Key': key } : {}),
        ...(activity ? { 'X-Activity-ID': activity.id } : {}),
      },
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
    });
    const data = response.status === 204 ? null : await response.json().catch(() => null);
    if (!response.ok) {
      outcome = 'failed';
      if (response.status === 401) window.dispatchEvent(new Event('session-expired'));
      const error = new ApiError(
        (response.status === 404 && path === '/sessions'
          ? 'The styled UI is available, but this backend does not provide the workspace session API yet. Connect the integrated backend to enter the workspace.'
          : null) || data?.error?.message ||
          (typeof data?.detail === 'string' ? data.detail : `Request failed (${response.status}).`),
        response.status,
        data?.error?.trace_id || response.headers.get('X-Trace-ID'),
      );
      error.data = data;
      throw error;
    }
    if (response.status !== 204 && (data === null || typeof data !== 'object')) {
      throw new ApiError(
        'The backend returned an unreadable response. Check the API URL and retry.',
        response.status,
      );
    }
    outcome = 'completed';
    activityResponse = data;
    return data;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new ApiError(
      error.name === 'AbortError'
        ? 'The request timed out. Refresh to check its status before retrying.'
        : 'Cannot reach the backend. Check that the API is running, then retry.',
      0,
    );
  } finally {
    clearTimeout(timeout);
    activity?.finish(outcome, activityResponse);
  }
}
const query = (params) =>
  new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined)).toString();
export const api = {
  officialCosts: () => request('/chat/official-costs'),
  tracing: () => request('/chat/tracing'),
  centralLogs: (hours = 1) => request(`/chat/central-logs${hours === 1 ? '' : `?hours=${hours}`}`),
  agentRouting: () => request('/chat/routing'),
  chatModels: () => request('/chat/models'),
  connectExternalModel: (body) => request('/chat/external-models', { method: 'POST', body }),
  externalModelOptions: (body) => request('/chat/external-model-options', { method: 'POST', body }),
  chatMetrics: () => request('/chat/metrics'),
  startChatEvaluation: (body) => request('/chat/evaluations', { method: 'POST', body }),
  chatEvaluation: (id) => request(`/chat/evaluations/${id}`),
  rateChatRun: (id, rating) => request(`/chat/runs/${id}/rating`, { method: 'POST', body: { rating } }),
  login: (body) => request('/sessions', { method: 'POST', body }),
  me: () => request('/sessions/me'),
  logout: () => request('/sessions/me', { method: 'DELETE' }),
  health: () => request('/health'),
  ready: () => request('/ready'),
  registryAgents: (offset = 0) => request(`/registry/agents?limit=20&offset=${offset}`),
  registerAgent: (body) => request('/registry/agents', { method: 'POST', body }),
  deliveryConnectionStatus: () => request('/registry/agents/delivery-connection'),
  deleteAgent: (id) => request(`/registry/agents/${id}`, { method: 'DELETE' }),
  connectDeliveryAgent: (id, api_token) => request(`/registry/agents/${id}/delivery-connection`, { method: 'POST', body: { api_token } }),
  disconnectDeliveryAgent: (id) => request(`/registry/agents/${id}/delivery-connection`, { method: 'DELETE' }),
  setAgentActive: (id, active) =>
    request(`/registry/agents/${id}/${active ? 'activate' : 'deactivate'}`, { method: 'POST' }),
  risks: (horizon) => request(`/inventory/risks?${query({ horizon_days: horizon })}`),
  cases: (offset = 0) => request(`/cases?limit=20&offset=${offset}`),
  case: (id) => request(`/cases/${id}`),
  approvedMemory: (itemId, limit = 5) =>
    request(`/memory/approved?${query({ item_id: itemId, limit })}`),
  supplierEmail: (id) => request(`/cases/${id}/supplier-email`),
  startSupplierWorkflow: (id, key) =>
    request('/supplier/workflows', { method: 'POST', body: { case_id: id }, key }),
  supplierWorkflow: (id) => request(`/supplier/workflows/${id}`),
  resumeSupplierWorkflow: (id) =>
    request(`/supplier/workflows/${id}/resume`, { method: 'POST', body: {} }),
  sales: (id, offset = 0) =>
    request(`/items/${id}/sales-history?days=30&limit=20&offset=${offset}`),
  compare: (id, quantity, expedited = false) =>
    request(`/items/${id}/suppliers?${query({ quantity, expedited })}`),
  start: (body, key) => request('/retail/workflows', { method: 'POST', body, key }),
  workflowModel: (id, model_profile) => request(`/retail/workflows/${id}/model`, { method: 'POST', body: { model_profile } }),
  workflow: (id) => request(`/retail/workflows/${id}`),
  select: (id, body) => request(`/retail/workflows/${id}/selection`, { method: 'POST', body }),
  resume: (id) => request(`/retail/workflows/${id}/resume`, { method: 'POST', body: {} }),
  decision: (id, role, body, key) =>
    request(`/cases/${id}/${role === 'supplier' ? 'supplier' : 'manager'}-decision`, {
      method: 'POST',
      body,
      key,
    }),
  prepare: (id, body, key) => request(`/cases/${id}/prepare`, { method: 'POST', body, key }),
  dispatch: (id, kind) => request(`/cases/${id}/dispatch/${kind}`, { method: 'POST' }),
  complete: (id, key) => request(`/cases/${id}/complete`, { method: 'POST', key }),
  chat: (body, activityOwnerId) => request('/chat', { method: 'POST', body, activityOwnerId }),
  supplierInventory: (id) => request(`/supplier/items/${id}/inventory`),
  supplierQuote: (id, quantity) => request(`/supplier/items/${id}/quote?${query({ quantity })}`),
  policy: () => request('/supplier/policy'),
};
export default api;
