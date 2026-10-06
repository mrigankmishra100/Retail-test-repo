import { useEffect, useRef, useState } from 'react';
import api from '../api';
import { Badge, ErrorNotice, Icon, Loading, Status, number, useAction, useResource } from '../ui';
import ChatRequest, { ChatQuotes, WorkflowReply } from './ChatWorkflow';

// Only explicit UI actions call mutation endpoints. Model text never becomes an approval.
export default function AgentChat({ session, thread, setThread, onChange, chatTarget, active, navigate }) {
  const retail = session.role === 'retail-manager';
  const storageKey = `agent-chat.request.${session.session_id}.${session.role}.${session.supplier_id || ''}`;
  const [selected, setSelected] = useState(() => sessionStorage.getItem(storageKey) || ''),
    [messages, setMessages] = useState([]), [input, setInput] = useState(''),
    [conversation, setConversation] = useState(null), [assessment, setAssessment] = useState(false),
    [itemId, setItemId] = useState(''), [quantity, setQuantity] = useState(''),
    [showRequests, setShowRequests] = useState(false), [workflow, setWorkflow] = useState(null),
    [view, setView] = useState('idle'), [useRequestContext, setUseRequestContext] = useState(false);
  const action = useAction(), end = useRef(null), lastTarget = useRef(null);
  const requests = useResource(() => active && showRequests ? api.cases() : Promise.resolve(null), [active, showRequests], active && showRequests ? 15000 : 0);
  const checkpoint = useResource(() => retail && thread && active ? api.workflow(thread) : Promise.resolve(null), [retail, thread, active], active && thread ? 10000 : 0);

  function say(message, role = 'assistant', extra = {}) {
    setMessages((current) => [...current, { id: crypto.randomUUID(), role, message, ...extra }].slice(-60));
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
    // A restored checkpoint must not reopen an old request during general chat.
    if (view === 'workflow' && !assessment && checkpoint.data.case) openRequest(checkpoint.data.case.case_id);
  }, [checkpoint.data]);
  useEffect(() => {
    if (active && messages.length) end.current?.scrollIntoView({ behavior: 'auto', block: 'nearest' });
  }, [messages, active]);

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
    const result = await action.run('conversation', () => api.chat({
      message: text, intent: 'conversation',
      ...(conversation ? { conversation_id: conversation } : {}),
      ...(useRequestContext && view === 'request' && thread && workflow?.case?.case_id === selected ? { workflow_thread_id: thread } : {}),
    }));
    if (result) {
      say(result.message, 'assistant', { mode: result.mode, used_tools: result.used_tools });
      if (result.conversation_id) setConversation(result.conversation_id);
    } else setInput(text);
  }
  function updateWorkflow(result) {
    setWorkflow(result);
    setThread(result.thread_id);
    setView('workflow');
    if (result.case) openRequest(result.case.case_id);
    onChange();
    say(result.message || 'The agent has updated your assessment.', 'assistant', { mode: result.mode });
  }
  async function start(event) {
    event.preventDefault();
    const body = { ...(itemId ? { item_id: itemId } : {}), ...(quantity ? { quantity: Number(quantity) } : {}) };
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
      say(itemId ? `Assess ${itemId}${quantity ? ` for ${quantity} units` : ' using the recommended quantity'}.` : 'Assess an at-risk item using the recommended quantity.', 'user');
      updateWorkflow(result);
    }
  }
  async function select(quote) {
    const result = await action.run(`select:${thread}:${quote.supplier_id}`, () => api.select(thread, { supplier_id: quote.supplier_id, quantity: quote.quantity }));
    if (result) {
      say(`I selected ${quote.supplier_name || quote.supplier_id} for ${number(quote.quantity)} units.`, 'user');
      updateWorkflow(result);
    }
  }
  async function resume() {
    const result = await action.run(`resume:${thread}`, () => api.resume(thread));
    if (result) updateWorkflow(result);
  }
  return (
    <section className="agent-chat chat-panel" aria-label="Agent conversation">
      <div className="chat-header">
        <span className="assistant-emblem"><Icon name="bot" /></span>
        <div><h2>{retail ? 'Your retail agent' : 'Your supplier agent'}</h2><p>From review to response, in one conversation</p></div>
        <button className="text-button" disabled={action.busy} onClick={() => {
          setMessages([]); setConversation(null); setUseRequestContext(false); setInput(''); setAssessment(false); setView('idle');
        }}>New chat</button>
      </div>
      <div className="chat-transcript">
        <div className="chat-welcome">
          <article className="message assistant welcome-message">
            <span className="message-avatar"><Icon name="bot" size={19} /></span>
            <div className="message-body">
              <strong>{retail ? 'Retail agent' : 'Supplier agent'}</strong>
              <h3>{retail ? 'Hi! Let’s keep your shelves stocked.' : 'Hi! Let’s review your incoming requests.'}</h3>
              <p>{retail
                ? 'I can help you check stock risks, compare suppliers, and prepare a replenishment request. We’ll review each step here, and you’ll approve the exact draft before anything is sent. What would you like to work on?'
                : 'I can help you review an assigned request, check availability and policy terms, and prepare a response. You’ll review and approve the response here before it is sent. Choose a request to get started.'}</p>
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
        </div>
        <div role="log" aria-live="polite" aria-label="Conversation messages">
          {messages.map((m) => <article className={`message ${m.role}`} key={m.id}>
            <div className="message-avatar"><Icon name={m.role === 'user' ? 'people' : 'bot'} size={17} /></div>
            <div className="message-body"><strong>{m.role === 'user' ? 'You' : retail ? 'Retail agent' : 'Supplier agent'}</strong><p>{m.message}</p>
              {m.mode && <Badge>{m.mode === 'llm_read_only' ? 'Model-assisted answer' : 'Verified workflow facts'}</Badge>}
              {m.used_tools?.length > 0 && <small>Checked with: {m.used_tools.join(', ')}</small>}
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
          {workflow.comparison && <ChatQuotes quotes={workflow.comparison.suppliers} recommendedSupplierId={workflow.comparison.recommended_supplier_id}
            onSelect={workflow.pending_input === 'selection' ? select : undefined} busy={action.busy || Boolean(checkpoint.error)} />}
          {workflow.can_resume && workflow.pending_input !== 'selection' && <button disabled={action.busy || Boolean(checkpoint.error) || workflow.status === 'delivery_reconciliation_required'} onClick={resume}>Continue assessment</button>}
        </WorkflowReply>}
        {view === 'request' && selected && active && <ChatRequest key={selected} id={selected} session={session} onChange={onChange} onMessage={say} />}
        {action.busy && <Loading label="The agent is checking your request…" />}
        <ErrorNotice error={action.error} />
        <div ref={end} />
      </div>
      <form className="chat-compose" onSubmit={send}>
        {retail && view === 'request' && workflow?.case?.case_id === selected && <label className="chat-context-choice"><input type="checkbox" disabled={action.busy} checked={useRequestContext} onChange={(e) => { setUseRequestContext(e.target.checked); setConversation(null); }} /> Use this request as context for my questions</label>}
        <div className="compose-input"><textarea aria-label="Message to agent" required rows="2" maxLength="4000" value={input} onChange={(e) => setInput(e.target.value)} disabled={action.busy} placeholder={retail ? 'Ask a question or ask to start an assessment…' : 'Ask to review incoming requests or check request status…'} />
          <button className="primary" disabled={action.busy || !input.trim()} aria-label="Send message"><Icon name="arrow" /></button>
        </div>
        <small>Approval and sending require the explicit controls in this chat. Your message alone cannot commit an order.</small>
      </form>
    </section>
  );
}
