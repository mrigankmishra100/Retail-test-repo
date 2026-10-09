import { useEffect, useRef, useState } from 'react';
import api from '../api';
import { Badge, ErrorNotice, Icon, useAction } from '../ui';
import { InventoryTable, QuoteCard } from './Workspace';

export default function Assistant({ session, thread, navigate }) {
  const [messages, setMessages] = useState([]),
    [input, setInput] = useState(''),
    [conversation, setConversation] = useState(null),
    [mode, setMode] = useState('conversation'),
    [caseId, setCaseId] = useState(''),
    [itemId, setItemId] = useState('ITEM001'),
    [quantity, setQuantity] = useState(100);
  const action = useAction(),
    bottom = useRef(null);
  const retail = session.role === 'retail-manager';
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }, [messages, action.busy]);
  async function send(text, intent = retail ? mode : 'help') {
    if (!text.trim() || action.busy) return;
    const body = {
      message: text.trim(),
      intent,
      ...(intent === 'conversation'
        ? {
            ...(conversation ? { conversation_id: conversation } : {}),
            ...(thread ? { workflow_thread_id: thread } : {}),
          }
        : {}),
      ...(intent === 'case_status' ? { case_id: caseId } : {}),
      ...(intent === 'supplier_quote' ? { item_id: itemId, quantity: Number(quantity) } : {}),
    };
    const result = await action.run('chat', () => api.chat(body));
    if (result) {
      setMessages((m) => [...m, { role: 'user', message: text }, { ...result, role: 'assistant' }]);
      setInput('');
      if (result.conversation_id) setConversation(result.conversation_id);
    }
  }
  function submit(e) {
    e.preventDefault();
    send(input, retail ? mode : mode === 'conversation' ? 'help' : mode);
  }
  return (
    <div className="chat-layout">
      <section className="chat-panel">
        <div className="chat-header">
          <span className="assistant-emblem">
            <Icon name="spark" />
          </span>
          <div>
            <h2>Your inventory assistant</h2>
            <p>
              {retail
                ? 'Connected retail conversation & verified facts'
                : 'Supplier-scoped read-only assistance'}
            </p>
          </div>
          <Badge>Read-only</Badge>
        </div>
        <div className="chat-messages" role="log" aria-live="polite">
          {messages.length === 0 && (
            <div className="chat-welcome">
              <span className="chat-welcome-icon">
                <Icon name="spark" size={34} />
              </span>
              <h2>What would you like to understand?</h2>
              <p>
                Get context for your next decision, grounded in the backend’s inventory and supplier
                services.
              </p>
              <div className="prompt-grid">
                {(retail
                  ? [
                      ['Which items need replenishment?', 'conversation'],
                      ['How can you help me manage inventory?', 'help'],
                      ['Explain the current inventory situation.', 'conversation'],
                    ]
                  : [['What can I do in my supplier workspace?', 'help']]
                ).map(([text, intent]) => (
                  <button disabled={action.busy} key={text} onClick={() => send(text, intent)}>
                    {text}
                    <Icon name="arrow" size={16} />
                  </button>
                ))}
              </div>
            </div>
          )}
          {messages.map((m, i) => (
            <article className={`message ${m.role}`} key={i}>
              <div className="message-avatar">
                <Icon name={m.role === 'assistant' ? 'spark' : 'people'} size={17} />
              </div>
              <div className="message-body">
                <strong>{m.role === 'assistant' ? 'Inventory assistant' : 'You'}</strong>
                <p>{m.message}</p>
                {m.role === 'assistant' && (
                  <div className="message-metadata">
                    <Badge>
                      {m.response_route === 'NL2SQL'
                        ? 'Verified data answer'
                        : m.response_route === 'BLOCKED'
                          ? 'Safety blocked'
                          : m.mode === 'llm_read_only'
                            ? 'Model-assisted'
                            : 'Deterministic facts'}
                    </Badge>
                    {m.used_tools?.length > 0 && (
                      <small>Verified with: {m.used_tools.join(', ')}</small>
                    )}
                  </div>
                )}
                {m.inventory?.at_risk_items?.length > 0 && (
                  <InventoryTable
                    items={m.inventory.at_risk_items}
                    navigate={navigate}
                    compact
                  />
                )}
                {m.quote && <QuoteCard quote={m.quote} />}{' '}
                {m.case && (
                  <button onClick={() => navigate('cases')}>
                    Review case {m.case.case_id.slice(0, 8)}
                  </button>
                )}
              </div>
            </article>
          ))}
          {action.busy && (
            <div className="chat-thinking" role="status">
              <span className="spinner" /> Checking verified context…
            </div>
          )}
          <div ref={bottom} />
        </div>
        <ErrorNotice error={action.error} />
        <form className="chat-compose" onSubmit={submit}>
          <div className="compose-controls">
            <label>
              Response mode
              <select
                value={retail ? mode : mode === 'conversation' ? 'help' : mode}
                onChange={(e) => setMode(e.target.value)}
                disabled={action.busy}
              >
                {retail && <option value="conversation">Retail conversation</option>}
                <option value="help">Workspace help</option>
                {retail && <option value="inventory_risks">Inventory risk facts</option>}
                <option value="case_status">Case status</option>
                {!retail && <option value="supplier_quote">Supplier quote</option>}
              </select>
            </label>
            {mode === 'case_status' && (
              <label>
                Case ID
                <input
                  required
                  placeholder="Case UUID"
                  pattern="[0-9a-fA-F-]{36}"
                  value={caseId}
                  onChange={(e) => setCaseId(e.target.value)}
                />
              </label>
            )}
            {mode === 'supplier_quote' && (
              <>
                <label>
                  Item
                  <input
                    required
                    pattern="ITEM[0-9]{3}"
                    value={itemId}
                    onChange={(e) => setItemId(e.target.value.toUpperCase())}
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
                    onChange={(e) => setQuantity(e.target.value)}
                  />
                </label>
              </>
            )}
          </div>
          <div className="compose-input">
            <textarea
              required
              aria-label="Message to inventory assistant"
              rows="2"
              maxLength="4000"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Ask about your inventory, suppliers, or a case…"
              disabled={action.busy}
            />
            <button
              className="primary"
              aria-label="Send message"
              disabled={!input.trim() || action.busy}
            >
              <Icon name="arrow" />
            </button>
          </div>
          <small>Chat cannot record approvals. Use Cases & approvals to make a decision.</small>
        </form>
      </section>
      <aside className="chat-context">
        <h3>In this workspace</h3>
        <div>
          <Icon name="box" />
          <strong>Verified facts</strong>
          <p>Answers use available inventory, supplier, and case services.</p>
        </div>
        <div>
          <Icon name="shield" />
          <strong>You're in control</strong>
          <p>Supplier selections, decisions, and dispatch have dedicated controls.</p>
        </div>
        <div>
          <Icon name="flow" />
          <strong>Workflow context</strong>
          <p>
            {thread
              ? `Current thread: ${thread}`
              : 'Start a replenishment assessment to give the retail conversation workflow context.'}
          </p>
        </div>
        <small>
          Model responses depend on backend configuration. Deterministic help remains available.
        </small>
      </aside>
    </div>
  );
}
