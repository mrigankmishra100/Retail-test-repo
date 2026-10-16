// Browser-session transcripts. Provider credentials are never part of a snapshot.
const prefix = 'retail.chats.v1.';
export const newSessionChat = () => ({ id: crypto.randomUUID(), title: 'New chat', snapshot: {} });
export function readSessionChats(sessionId) {
  try {
    const saved = JSON.parse(sessionStorage.getItem(prefix + sessionId));
    if (Array.isArray(saved?.chats) && saved.chats.length && saved.chats.some(c => c.id === saved.activeId)) {
      return { ...saved, chats: saved.chats.map(chat => ({ ...chat, snapshot: {
        ...chat.snapshot, busy: false,
        messages: (chat.snapshot.messages || []).map(m => m.pending ? { ...m, pending: false,
          message: 'The connection ended before this answer was confirmed. Check request status before retrying.' } : m),
        activityRuns: (chat.snapshot.activityRuns || []).map(run => run.client_state === 'running'
          ? { ...run, client_state: 'interrupted', status: 'interrupted' } : run),
      } })) };
    }
  } catch { /* A missing or invalid browser cache starts a fresh chat. */ }
  const chat = newSessionChat();
  return { activeId: chat.id, chats: [chat] };
}
export function saveSessionChats(sessionId, state) {
  try { sessionStorage.setItem(prefix + sessionId, JSON.stringify(state)); return true; }
  catch { return false; } // Keep the full transcript in memory if browser storage is full.
}
export function clearSessionChats() {
  for (const key of Object.keys(sessionStorage)) if (key.startsWith(prefix)) sessionStorage.removeItem(key);
}
