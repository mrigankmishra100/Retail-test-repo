import { useEffect, useRef, useState } from 'react';
import api from '../api';
import { Badge, ErrorNotice, Icon, Loading, Status, number, useAction, useResource } from '../ui';
import ChatRequest, { ChatQuotes, WorkflowReply } from './ChatWorkflow';
import { RunMetrics } from './ChatEvaluation';
import ChatActivity, { useActivityRuns } from './ChatActivity';
import PromptNavigator from './PromptNavigator';
import Markdown from '../Markdown';
import ExternalModelDialog from './ExternalModelDialog';
import SecurityDecision from './SecurityDecision';
import QueryDetails from './QueryDetails';
import WorkflowHistory from './WorkflowHistory';
import { nextTimelineOrder } from '../activity-client';

// Only explicit UI actions call mutation endpoints. Model text never becomes an approval.
export default function AgentChat({ session, thread, setThread, onChange, chatTarget, active, navigate, chatId, initialChat = {}, onSaveChat, onNewChat }) {
  const retail = session.role === 'retail-manager';
  const storageKey = `agent-chat.request.${session.session_id}.${session.role}.${session.supplier_id || ''}`;
  const [selected, setSelected] = useState(() => initialChat.selected || ''),
    [messages, setMessages] = useState(initialChat.messages || []), [input, setInput] = useState(initialChat.input || ''),
    [conversation, setConversation] = useState(initialChat.conversation || null), [assessment, setAssessment] = useState(initialChat.assessment || false),
    [itemId, setItemId] = useState(initialChat.itemId || ''), [quantity, setQuantity] = useState(initialChat.quantity || ''),
    [showRequests, setShowRequests] = useState(initialChat.showRequests || false), [workflow, setWorkflow] = useState(null),
    [view, setView] = useState(initialChat.view || 'idle'), [useRequestContext, setUseRequestContext] = useState(initialChat.useRequestContext || false);
  const action = useAction(), end = useRef(null), lastTarget = useRef(null), composer = useRef(null), transcript = useRef(null);
  const followReply = useRef(true);
  const catalog = useResource(() => active && retail ? api.chatModels() : Promise.resolve(null), [active, retail]);
  const [modelProfile, setModelProfile] = useState(initialChat.modelProfile || ''), [fileSearch, setFileSearch] = useState(initialChat.fileSearch || false);
  const [externalDialog, setExternalDialog] = useState(false), [connectedModels, setConnectedModels] = useState([]);
  const models = [...(catalog.data?.models || []), ...connectedModels.filter((model) => !catalog.data?.models.some((m) => m.id === model.id))];
  const activityReset = 0;
  const activityRuns = useActivityRuns(true, activityReset, initialChat.activityRuns || []);
  const busy = Boolean(action.busy) || activityRuns.some(run => run.client_state === 'running');
  useEffect(() => {
    onSaveChat?.(chatId, { messages, input, conversation, selected, assessment, itemId, quantity,
      showRequests, view, useRequestContext, modelProfile, fileSearch, thread, activityRuns, busy });
  }, [chatId, onSaveChat, messages, input, conversation, selected, assessment, itemId, quantity,
    showRequests, view, useRequestContext, modelProfile, fileSearch, thread, activityRuns, busy]);
  function openEvaluation() { navigate('evaluation'); }
  useEffect(() => {
    if (catalog.data && !modelProfile) setModelProfile(catalog.data.default_profile || '');
  }, [catalog.data]);
  const selectedModel = models.find((m) => m.id === modelProfile);
  async function changeModel(id, rag = false, connectedProfile = null) {
    if (thread && id !== modelProfile) {
      const result = await action.run(`model:${thread}`, () => api.workflowModel(thread, id));
      if (!result) return false;
      setWorkflow(result);
    }
    setModelProfile(id); setFileSearch(rag); setConversation(null);
    if (messages.length) say(`Model changed to ${connectedProfile?.label || models.find(model => model.id === id)?.label || 'the selected model'}. The next question starts with fresh model context.`);
    setUseRequestContext(false);
    return true;
  }
  const requests = useResource(() => active && showRequests ? api.cases() : Promise.resolve(null), [active, showRequests], active && showRequests ? 15000 : 0);
  const checkpoint = useResource(() => retail && thread && active ? api.workflow(thread) : Promise.resolve(null), [retail, thread, active], active && thread ? 10000 : 0);

  function say(message, role = 'assistant', extra = {}) {
    followReply.current = true;
    setMessages((current) => [...current, { id: crypto.randomUUID(), timeline_order: nextTimelineOrder(), role, message, ...extra }]);
  }
  function openRequest(id) {
    setConversation(null);
    setUseRequestContext(false);
    setView('request');
    setShowRequests(false);
    setSelected(id);
    sessionStorage.setItem(storageKey, id);
    setAssessment(false);
  }
  useEffect(() => {
    if (!chatTarget || lastTarget.current === chatTarget.navigationId) return;
    lastTarget.current = chatTarget.navigationId;
    if (chatTarget.case_id) {
      openRequest(chatTarget.case_id);
      say(`Let's review request #${chatTarget.case_id.slice(0, 8)}. I will load its saved terms and latest status below.`);
    } else if (retail && chatTarget.item_id) {
      setConversation(null);
      setUseRequestContext(false);
      setItemId(chatTarget.item_id);
      setQuantity(chatTarget.recommended_quantity || '');
      setAssessment(true);
      setView('assessment');
      say(`Let's assess ${chatTarget.item_id}. Check the assessment details below before starting.`);
    }
  }, [chatTarget]);
  useEffect(() => {
    if (!checkpoint.data) return;
    setWorkflow(checkpoint.data);
    if (checkpoint.data.model_profile) setModelProfile(checkpoint.data.model_profile);
    // A restored checkpoint must not reopen an old request during general chat.
    if (view === 'workflow' && !assessment && checkpoint.data.case) openRequest(checkpoint.data.case.case_id);
  }, [checkpoint.data]);
  useEffect(() => {
    if (active && messages.length && followReply.current) {
      const latest = end.current?.parentElement?.querySelector('[role="log"] > article:last-child');
      latest?.scrollIntoView({ behavior: 'auto', block: 'start' });
    }
  }, [messages.at(-1)?.id, messages.at(-1)?.pending, active]);
  useEffect(() => {
    if (!composer.current) return;
    composer.current.style.height = 'auto';
    composer.current.style.height = `${Math.min(composer.current.scrollHeight, 144)}px`;
  }, [input, active]);

  function prepareAssessment(text = 'Start a replenishment assessment') {
    setConversation(null);
    setUseRequestContext(false);
    setView('assessment');
    setShowRequests(false);
    say(text, 'user');
    setAssessment(true);
    say('I can check inventory risk and compare feasible suppliers. Leave the fields blank to use the agent’s recommendation. Nothing will be sent without your approval.');
  }
  function listRequests(text = retail ? 'Show my request updates' : 'Review my incoming requests') {
    setConversation(null);
    setUseRequestContext(false);
    setView('requests');
    say(text, 'user');
    setShowRequests(true);
    requests.reload();
    say('Choose a request below. Its saved terms, review controls, and latest outcome will stay in this chat.');
  }
  async function send(event) {
    event?.preventDefault();
    const text = input.trim();
    if (!text || action.busy) return;
    setInput('');
    if (/^(hi|hello|hey|good\s+(morning|afternoon|evening))[!.\s]*$/i.test(text)) {
      setConversation(null);
      setUseRequestContext(false);
      setView('idle');
      setAssessment(false);
      say(text, 'user');
      say(retail ? 'Hi! How can I help? We can check stock, start a replenishment assessment, or review a request.'
        : 'Hi! How can I help? We can review incoming requests and check availability before you decide.');
      return;
    }
    // Workflow entry is offered for review, not executed from a language guess.
    if (retail && /\b(start|assess|replenish|reorder)\b/i.test(text)) {
      const item = text.match(/\bITEM\d{3}\b/i)?.[0];
      setItemId(item?.toUpperCase() || '');
      setQuantity('');
      prepareAssessment(text);
      return;
    }
    if (/\b(approve|reject|send|dispatch|complete|confirm)\b/i.test(text)) {
      say(text, 'user');
      say('Please use the review and decision controls on the current request below. A chat message alone never approves, rejects, or sends a request.');
      if (selected) setView('request');
      else { setShowRequests(true); setView('requests'); }
      return;
    }
    if (/\b(requests?|status|updates?|incoming)\b/i.test(text)) {
      listRequests(text);
      return;
    }
    say(text, 'user');
    if (!retail) {
      setShowRequests(true);
      setView('requests');
      say('Open an assigned request below to ask the supplier agent to check stock, policy, and quote feasibility. You can review and approve its response here. For request history, use Request Activity.');
      return;
    }
    const answerId = crypto.randomUUID();
    say('', 'assistant', { id: answerId, pending: true });
    const result = await action.run('conversation', () => api.chat({
      message: text, intent: 'conversation',
      ...(modelProfile ? { model_profile: modelProfile, use_file_search: fileSearch } : {}),
      ...(conversation ? { conversation_id: conversation } : {}),
      ...(useRequestContext && view === 'request' && thread && workflow?.case?.case_id === selected ? { workflow_thread_id: thread } : {}),
    }, answerId));
    if (result) {
      setMessages((current) => current.map((entry) => entry.id === answerId ? { ...entry, pending: false, message: result.message, mode: result.mode, llm_status: result.llm_status, used_tools: result.used_tools, metrics: result.metrics, guardrail_status: result.guardrail_status, security_event_id: result.security_event_id, security_details: result.security_details } : entry));
      if (result.conversation_id) setConversation(result.conversation_id);
    } else {
      setMessages((current) => current.map((entry) => entry.id === answerId ? { ...entry, pending: false, message: 'This request did not return an answer. See the error below before retrying.' } : entry));
      setInput(text);
    }
  }
  function updateWorkflow(result) {
    setWorkflow(result);
    setThread(result.thread_id);
    setView('workflow');
    if (result.case) openRequest(result.case.case_id);
    onChange();
  }
  async function start(event) {
    say(itemId ? `Assess ${itemId}${quantity ? ` for ${quantity} units` : ' using the recommended quantity'}.` : 'Assess an at-risk item using the recommended quantity.', 'user');
    event.preventDefault();
    const body = { ...(modelProfile ? { model_profile: modelProfile } : {}), ...(itemId ? { item_id: itemId } : {}), ...(quantity ? { quantity: Number(quantity) } : {}) };
    // Preserve uncertain starts across refreshes. Reusing the same key recovers
    // the existing assessment instead of creating a duplicate request.
    const pendingKey = `${storageKey}.start.${JSON.stringify(body)}`;
    const result = await action.run(`start:${JSON.stringify(body)}`, (key) => {
      const stableKey = sessionStorage.getItem(pendingKey) || key;
      sessionStorage.setItem(pendingKey, stableKey);
      return api.start(body, stableKey);
    });
    if (result) {
      sessionStorage.removeItem(pendingKey);
      setSelected('');
      sessionStorage.removeItem(storageKey);
      setAssessment(false);
      updateWorkflow(result);
    }
  }
  async function select(quote) {
    say(`Select ${quote.supplier_name || quote.supplier_id} for ${number(quote.quantity)} units.`, 'user');
    const result = await action.run(`select:${thread}:${quote.supplier_id}`, () => api.select(thread, { supplier_id: quote.supplier_id, quantity: quote.quantity }));
    if (result) {
      updateWorkflow(result);
    }
  }
  async function resume() {
    const result = await action.run(`resume:${thread}`, () => api.resume(thread));
    if (result) updateWorkflow(result);
  }
  const transcriptEntries = [...messages, ...activityRuns.filter((run) => !run.client_message_id).map((run) => ({
    id: run.id, timeline_order: run.timeline_order, role: 'assistant', workflowRun: run,
  }))].sort((a, b) => a.timeline_order - b.timeline_order);
  return (
    <div className="chat-experience">
    <section className="agent-chat chat-panel" aria-label="Agent conversation">
      <div className="chat-header">
        <h2>{retail ? 'Retail agent' : 'Supplier agent'}</h2>
      {retail && <div className="chat-model-bar">
        <label><span className="sr-only">Chat model</span><select aria-label="Chat model" title="Switching starts fresh model context, preserves this transcript, and applies to remaining workflow AI steps." value={modelProfile} disabled={action.busy || catalog.loading} onChange={(e) => e.target.value === '__external__' ? setExternalDialog(true) : changeModel(e.target.value)}>
          {!models.length && <option value="">Choose a model</option>}
          {models.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
          <option value="__external__">External model…</option>
        </select></label>
        <label title={selectedModel?.file_search ? 'Search configured policy vector stores using OCI built-in File Search' : 'Requires configured vector stores and a compatible model'}>
          <input type="checkbox" checked={fileSearch} disabled={action.busy || !selectedModel?.file_search || !catalog.data?.enabled} onChange={(e) => setFileSearch(e.target.checked)} /> File Search / RAG</label>
        <button type="button" className="evaluation-shortcut" aria-label="Evaluation & metrics" title="Evaluation & metrics" onClick={openEvaluation}><Icon name="chart" size={15} /><span className="evaluation-shortcut-label">Metrics</span></button>
        <ErrorNotice error={catalog.error} retry={catalog.reload} />
      </div>}
      {externalDialog && <ExternalModelDialog onClose={() => setExternalDialog(false)} onConnected={async (profile) => {
        setConnectedModels((current) => [...current, profile]);
        const changed = await changeModel(profile.id, false, profile);
        catalog.reload();
        return changed;
      }} />}
        <button className="text-button chat-new" disabled={busy} onClick={onNewChat}>New chat</button>
      </div>
      <div className="chat-scroll-region">
      <PromptNavigator messages={messages} transcriptRef={transcript} active={active} onNavigate={() => { followReply.current = false; }} />
      <div className="chat-transcript" ref={transcript} onWheel={() => { followReply.current = false; }} onTouchMove={() => { followReply.current = false; }}>
        {!messages.length && view === 'idle' && <div className="chat-welcome">
          <article className="message assistant welcome-message">
            <span className="message-avatar"><Icon name="bot" size={19} /></span>
            <div className="message-body">
              <strong>{retail ? 'Retail agent' : 'Supplier agent'}</strong>
              <h3>{retail ? 'Hi! Let’s keep your shelves stocked.' : 'Hi! Let’s review your incoming requests.'}</h3>
              <p>{retail
                ? 'Check stock, compare suppliers, or prepare a replenishment request.'
                : 'Review requests, check policy terms, or prepare a supplier response.'}</p>
            </div>
          </article>
          <div className="prompt-grid">
            {retail && <button disabled={action.busy} onClick={() => prepareAssessment()}>Start a replenishment assessment <Icon name="arrow" size={16} /></button>}
            <button disabled={action.busy} onClick={() => listRequests()}>{retail ? 'Show my request updates' : 'Review my incoming requests'} <Icon name="arrow" size={16} /></button>
            <button onClick={() => {
              say('How does approval work?', 'user');
              say('Review the saved draft and commercial terms, confirm you have read them, then select Approve draft or Reject draft. Sending is a separate explicit action. Changed drafts require a fresh review.');
            }}>How does approval work? <Icon name="arrow" size={16} /></button>
          </div>
        </div>}
        <div role="log" aria-live="polite" aria-label="Conversation messages">
          {transcriptEntries.map((m) => <article className={`message ${m.role}`} key={m.id} id={`message-${m.id}`} data-message-id={m.id} tabIndex={-1}>
            <div className="message-avatar"><Icon name={m.role === 'user' ? 'people' : 'bot'} size={17} /></div>
            <div className="message-body"><strong>{m.role === 'user' ? 'You' : retail ? 'Retail agent' : 'Supplier agent'}</strong>
              {m.workflowRun && <WorkflowHistory run={m.workflowRun} />}
              {m.role === 'assistant' && <SecurityDecision details={m.security_details} status={m.guardrail_status} />}
              {m.message && (m.role === 'assistant' ? (!['BLOCKED', 'FAILED'].includes(m.guardrail_status) && <Markdown>{m.message}</Markdown>) : <p>{m.message}</p>)}
              {m.mode && !['BLOCKED', 'FAILED'].includes(m.guardrail_status) && <Badge>{['unavailable', 'disabled', 'limit_reached'].includes(m.llm_status) ? 'Answer unavailable' : m.mode === 'llm_read_only' ? 'Model-assisted answer' : 'Verified workflow facts'}</Badge>}
              {m.guardrail_status === 'PASSED' && <small className="security-indicator">Prompt injection check passed</small>}
              {m.metrics?.queries?.length > 0 && <QueryDetails queries={m.metrics.queries} />}
              {m.used_tools?.length > 0 && <small>Checked with: {m.used_tools.join(', ')}</small>}
              {m.role === 'assistant' && <ChatActivity runs={m.workflowRun ? [m.workflowRun] : activityRuns.filter((run) => run.client_message_id === m.id)} answerStatus={m.llm_status} guardrailStatus={m.guardrail_status} />}
              {m.metrics && <details className="chat-answer-metrics"><summary>Response metrics & sources</summary><RunMetrics metrics={m.metrics} compact /></details>}
            </div>
          </article>)}
        </div>
        {view === 'idle' && selected && <button className="chat-resume" onClick={() => openRequest(selected)}>Continue saved request #{selected.slice(0, 8)}</button>}
        {view === 'idle' && !selected && thread && <button className="chat-resume" onClick={() => {
          if (workflow?.case) openRequest(workflow.case.case_id);
          else { setView('workflow'); checkpoint.reload(); }
        }}>Continue saved assessment</button>}
        {view === 'assessment' && assessment && retail && <WorkflowReply label="Assessment details" step="Retail agent">
          <p className="chat-step-prompt">Which item should I check? Leave this blank and I’ll assess an at-risk item.</p>
          <form className="form-toolbar" onSubmit={start}>
            <label>Item ID<input aria-label="Assessment item ID" pattern="ITEM[0-9]{3}" placeholder="Auto-select an at-risk item" value={itemId} onChange={(e) => setItemId(e.target.value.toUpperCase())} disabled={action.busy} /></label>
            <label>Quantity<input aria-label="Assessment quantity" type="number" min="1" max="1000000" step="1" placeholder="Use recommendation" value={quantity} onChange={(e) => setQuantity(e.target.value)} disabled={action.busy} /></label>
            <button className="primary" disabled={action.busy}>Ask agent to assess</button>
          </form>
          {thread && <p className="muted">This starts a new assessment. Existing requests remain in Request Activity.</p>}
        </WorkflowReply>}
        {view === 'requests' && showRequests && <WorkflowReply label="Available requests" step="Choose a request to review">
          <h3>{retail ? 'Your requests' : 'Your assigned requests'}</h3>
          <ErrorNotice error={requests.error} retry={requests.reload} />
          {requests.loading ? <Loading /> : requests.data?.items.map((c) => <div className="activity-row" key={c.case_id}>
            <div><strong>{c.item_id} · {number(c.recommended_quantity)} units</strong><small>Request #{c.case_id.slice(0, 8)}</small></div>
            <Status value={c.status} /><button onClick={() => openRequest(c.case_id)}>Review request</button>
          </div>)}
          {requests.data?.items.length === 0 && <p>No requests are available for your workspace yet.</p>}
          {requests.data?.has_more && <button onClick={() => navigate('activity')}>View all requests</button>}
        </WorkflowReply>}
        {view === 'workflow' && <ErrorNotice error={checkpoint.error} retry={checkpoint.reload} />}
        {view === 'workflow' && checkpoint.error?.status === 404 && <button onClick={() => { setThread(null); setWorkflow(null); setView('idle'); }}>Clear expired assessment</button>}
        {view === 'workflow' && workflow && retail && <WorkflowReply label="Agent assessment" step="Here’s what I found">
          <Status value={workflow.status} />
          <p>{workflow.message}</p>{workflow.advisory && <p className="callout">{workflow.advisory}</p>}
          {workflow.llm_status === 'unavailable' && <p className="callout" role="status">Inventory and supplier checks completed, but the selected model could not produce its recommendation. Review Metrics or reconnect the model.</p>}
          {workflow.comparison && <ChatQuotes quotes={workflow.comparison.suppliers} recommendedSupplierId={workflow.comparison.recommended_supplier_id}
            onSelect={workflow.pending_input === 'selection' ? select : undefined} busy={action.busy || Boolean(checkpoint.error)} />}
          {workflow.can_resume && workflow.pending_input !== 'selection' && <button disabled={action.busy || Boolean(checkpoint.error) || workflow.status === 'delivery_reconciliation_required'} onClick={resume}>Continue assessment</button>}
        </WorkflowReply>}
        {view === 'request' && selected && active && <ChatRequest key={selected} id={selected} session={session} onChange={onChange} />}

        <ErrorNotice error={action.error} />
        <div ref={end} />
      </div>
      </div>
      <form className="chat-compose" onSubmit={send}>
        {retail && view === 'request' && workflow?.case?.case_id === selected && <label className="chat-context-choice"><input type="checkbox" disabled={action.busy} checked={useRequestContext} onChange={(e) => { setUseRequestContext(e.target.checked); setConversation(null); }} /> Use this request as context for my questions</label>}
        <div className="compose-input"><textarea ref={composer} aria-label="Message to agent" aria-describedby="compose-hint" required rows="1" maxLength="12000" value={input} onChange={(e) => setInput(e.target.value)} onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing && !action.busy) { e.preventDefault(); e.currentTarget.form.requestSubmit(); }
        }} disabled={action.busy} placeholder={retail ? 'Ask your agent…' : 'Ask about your requests…'} />
          <button className="primary" disabled={action.busy || !input.trim()} aria-label="Send message"><Icon name="arrow" /></button>
        </div>
        <small id="compose-hint">Enter to send · Shift+Enter for a new line · Orders require explicit approval.</small>
      </form>
    </section>
    </div>
  );
}
