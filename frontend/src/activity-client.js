import { workflowSnapshot } from './workflow-history';
// Optional metadata polling. Business requests are never retried by this observer.
const listeners = new Set();
const recent = new Map();
let generation = 0;
let timelineOrder = 0;
export const nextTimelineOrder = () => { timelineOrder = Math.max(timelineOrder + 1, Date.now() * 1000); return timelineOrder; };
export const hasRunningActivity = () => [...recent.values()].some(run => run.client_state === 'running');
export const clearActivity = () => { recent.clear(); ++generation; };
export function observeActivity(listener) {
  listeners.add(listener);
  for (const run of recent.values()) listener(run);
  return () => listeners.delete(listener);
}
const title = (path) => path === '/chat' ? 'Agent response'
  : path.endsWith('/supplier-email') ? 'Draft preview'
  : path.includes('/workflows') ? (path.startsWith('/supplier') ? 'Supplier workflow' : 'Retail workflow')
  : path.includes('decision') ? 'Record decision'
  : path.includes('/dispatch/') ? 'Publish approved notification'
  : path.endsWith('/complete') ? 'Complete request' : 'Prepare draft';

export function startActivity({ base, token, path, method, activityOwnerId, isCurrentSession }) {
  const eligible = (method === 'POST' && /^\/(chat|(?:retail|supplier)\/workflows(?:\/[^/]+\/(?:selection|resume|model))?|cases\/[^/]+\/(?:prepare|manager-decision|supplier-decision|complete|dispatch\/(?:request|response)))$/.test(path))
    || (method === 'GET' && /^\/cases\/[^/]+\/supplier-email$/.test(path));
  if (!token || !listeners.size || !eligible) return null;
  const id = crypto.randomUUID(), started = performance.now();
  const requestGeneration = generation;
  const isCurrent = () => requestGeneration === generation && isCurrentSession();
  let timer, finished = false, revision = 0;
  let snapshot = { id, timeline_order: nextTimelineOrder(), client_message_id: activityOwnerId, label: title(path), status: 'running', steps: [], elapsed_ms: 0, client_state: 'running' };
  const emit = () => {
    if (!isCurrent()) return;
    recent.set(id, { ...snapshot });
    // The active chat retains all recorded runs until the user switches chats.
    for (const listener of listeners) listener({ ...snapshot });
  };
  async function poll(final = false) {
    if (!isCurrent() || !listeners.size || (finished && !final)) return;
    const version = ++revision;
    const controller = new AbortController(), timeout = setTimeout(() => controller.abort(), 2500);
    try {
      const response = await fetch(`${base}/activity/${id}`, {
        signal: controller.signal, headers: { Authorization: `Bearer ${token}` }, cache: 'no-store',
      });
      if (response.ok) {
        const data = await response.json();
        if (version === revision && (!finished || final) && data.id === id && Array.isArray(data.steps)) {
          snapshot = { ...snapshot, ...data, client_message_id: activityOwnerId, telemetry: 'available' };
          emit();
        }
      }
    } catch { /* Progress is best-effort; the original request owns its outcome. */ }
    finally {
      clearTimeout(timeout);
      if (!finished && listeners.size) timer = setTimeout(poll, 600);
    }
  }
  emit();
  timer = setTimeout(poll, 150);
  return {
    id,
    finish(outcome, response) {
      finished = true; clearTimeout(timer); ++revision;
      snapshot = { ...snapshot, workflow_snapshot: outcome === 'completed' && !activityOwnerId ? workflowSnapshot(path, response) : null, status: outcome, client_state: outcome,
        duration_ms: performance.now() - started, elapsed_ms: performance.now() - started };
      emit();
      // A final read captures steps even when execution finished between polls.
      void poll(true);
    },
  };
}
