import { useEffect, useRef, useState } from 'react';
import api from '../api';
import { Badge, ErrorNotice, Icon, Loading } from '../ui';
import Markdown from '../Markdown';
import './distributor-chat.css';

const prompts = [
  'Compare standard delivery options across distributors.',
  'What are BlueDart’s delivery timelines and coverage?',
  'Compare COD limits and settlement timelines.',
  'How do Delhivery and DTDC handle returns?',
];

// The backend chooses the active knowledge document. Browser role selection is
// not authorization: these endpoints require a server-verified Distributor session.
export default function DistributorChat({ chatId, initialChat = {}, onSaveChat, onNewChat }) {
  const [messages, setMessages] = useState(initialChat.messages || []);
  const [input, setInput] = useState(initialChat.input || '');
  const [knowledge, setKnowledge] = useState(null);
  const [knowledgeError, setKnowledgeError] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  const composer = useRef(null), bottom = useRef(null), sending = useRef(false);
  const mounted = useRef(false);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    let cancelled = false, timer;
    async function check() {
      try {
        const data = await api.distributorKnowledge();
        if (!data || typeof data.ready !== 'boolean') throw new Error('The knowledge service returned an unreadable response.');
        if (cancelled) return;
        setKnowledge(data); setKnowledgeError(null);
        if (!data.ready) timer = setTimeout(check, 10000);
      } catch (failure) {
        if (!cancelled) { setKnowledge(null); setKnowledgeError(failure); }
      } finally { if (!cancelled) setLoading(false); }
    }
    setLoading(true);
    void check();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [revision]);
  useEffect(() => { onSaveChat?.(chatId, { messages, input, busy }); }, [chatId, messages, input, busy, onSaveChat]);
  useEffect(() => { bottom.current?.scrollIntoView({ block: 'nearest' }); }, [messages, busy]);
  useEffect(() => {
    if (!composer.current) return;
    composer.current.style.height = 'auto';
    composer.current.style.height = `${Math.min(composer.current.scrollHeight, 144)}px`;
  }, [input]);

  async function send(event) {
    event.preventDefault();
    const question = input.trim();
    if (sending.current || !knowledge?.ready || !question || question.length > 4000) return;
    sending.current = true; setBusy(true); setError(null);
    try {
      const answer = await api.distributorAsk(question);
      if (typeof answer?.answer !== 'string' || !answer.answer.trim() || !Array.isArray(answer.sources)
        || answer.sources.some(source => typeof source.document_name !== 'string' || typeof source.excerpt !== 'string')) {
        throw new Error('The agent returned an unreadable answer. Your question has been kept; please try again later.');
      }
      if (!mounted.current) return;
      setMessages(current => [...current,
        { id: crypto.randomUUID(), role: 'user', message: question },
        { id: crypto.randomUUID(), role: 'assistant', message: answer.answer, sources: answer.sources,
          demo_data: answer.demo_data, answer_status: answer.answer_status },
      ]);
      setInput('');
    } catch (failure) { if (mounted.current) setError(failure); }
    finally { sending.current = false; if (mounted.current) setBusy(false); }
  }

  return <div className="chat-experience distributor-chat">
    <section className="agent-chat chat-panel" aria-label="Distributor conversation">
      <h1 className="sr-only">Distributor workspace</h1>
      <div className="chat-header">
        <h2>Distributor agent</h2>
        <button className="text-button chat-new" disabled={busy} onClick={onNewChat}>New chat</button>
      </div>
      <div className="chat-transcript">
        {loading && <Loading label="Checking distributor knowledge…" />}
        <ErrorNotice error={knowledgeError} retry={() => setRevision(value => value + 1)} />
        {!loading && knowledge && !knowledge.ready && <div className="callout" role="status">
          {knowledge.message || 'Distributor knowledge is being prepared. Chat will be available when it is ready.'}
        </div>}
        {knowledge?.demo_data && <p className="distributor-demo" role="note">Demo knowledge: fictional examples, not actual distributor policies.</p>}
        {!messages.length && <div className="chat-welcome">
          <article className="message assistant welcome-message">
            <span className="message-avatar"><Icon name="bot" size={19} /></span>
            <div className="message-body">
              <strong>Distributor agent</strong>
              <h3>Hi! Let’s find the right delivery option.</h3>
              <p>Ask about delivery services, coverage, timelines, and policies. Compare the documented options and check the sources.</p>
            </div>
          </article>
          <div className="prompt-grid">{prompts.map(prompt => <button key={prompt} disabled={busy || !knowledge?.ready}
            onClick={() => { setInput(prompt); composer.current?.focus(); }}>{prompt}<Icon name="arrow" size={16} /></button>)}</div>
        </div>}
        <div role="log" aria-live="polite" aria-label="Distributor messages">
          {messages.map(entry => <article className={`message ${entry.role}`} key={entry.id}>
            <span className="message-avatar"><Icon name={entry.role === 'user' ? 'people' : 'bot'} size={17} /></span>
            <div className="message-body">
              <strong>{entry.role === 'user' ? 'You' : 'Distributor agent'}</strong>
              {entry.role === 'assistant' ? <Markdown>{entry.message}</Markdown> : <p>{entry.message}</p>}
              {entry.demo_data && <Badge tone="warning">Demo knowledge</Badge>}
              {entry.answer_status === 'insufficient_information' && <Badge>Insufficient documented information</Badge>}
              {entry.sources?.length > 0 && <details className="distributor-sources">
                <summary>Sources ({entry.sources.length})</summary>
                {entry.sources.map((source, index) => <section key={`${source.source_id}-${index}`}>
                  <strong>{source.document_name}{source.page ? ` · Page ${source.page}` : ''} [{source.source_id}]</strong>
                  <blockquote>{source.excerpt}</blockquote>
                </section>)}
              </details>}
            </div>
          </article>)}
        </div>
        {busy && <p role="status" className="distributor-thinking"><span className="spinner" /> Checking the distributor document…</p>}
        <ErrorNotice error={error} />
        <div ref={bottom} />
      </div>
      <form className="chat-compose" onSubmit={send}>
        <div className="compose-input">
          <textarea ref={composer} aria-label="Message to distributor" aria-describedby="distributor-compose-hint"
            rows="1" required maxLength={4000} value={input} disabled={busy || !knowledge?.ready}
            placeholder="Ask about delivery options or distributor policies…"
            onChange={event => setInput(event.target.value)} onKeyDown={event => {
              if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
                event.preventDefault(); event.currentTarget.form.requestSubmit();
              }
            }} />
          <button type="submit" className="primary" aria-label="Send message" disabled={busy || !knowledge?.ready || !input.trim()}><Icon name="arrow" size={18} /></button>
        </div>
        <small id="distributor-compose-hint">Answers use the prepared knowledge document. Each question is independent. Enter to send · Shift+Enter for a new line.</small>
      </form>
    </section>
  </div>;
}
