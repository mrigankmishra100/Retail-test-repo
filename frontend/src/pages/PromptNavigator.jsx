import { useEffect, useRef, useState } from 'react';
import './prompt-navigation.css';

export default function PromptNavigator({ messages, transcriptRef, active, onNavigate }) {
  const prompts = messages.filter((message) => message.role === 'user');
  const [current, setCurrent] = useState(null), [preview, setPreview] = useState(null);
  const rail = useRef(null);
  useEffect(() => {
    const transcript = transcriptRef.current;
    if (!active || !transcript) return;
    let frame;
    const update = () => {
      const top = transcript.getBoundingClientRect().top + 50;
      let selected = prompts[0]?.id;
      for (const prompt of prompts) {
        const element = document.getElementById(`message-${prompt.id}`);
        if (element && element.getBoundingClientRect().top <= top) selected = prompt.id;
      }
      if (transcript.scrollTop + transcript.clientHeight >= transcript.scrollHeight - 3) selected = prompts.at(-1)?.id;
      setCurrent(selected);
    };
    const schedule = () => { cancelAnimationFrame(frame); frame = requestAnimationFrame(update); };
    const observer = new ResizeObserver(schedule);
    observer.observe(transcript);
    const log = transcript.querySelector('[role="log"]');
    if (log) observer.observe(log);
    transcript.addEventListener('scroll', schedule, { passive: true });
    schedule();
    return () => { cancelAnimationFrame(frame); observer.disconnect(); transcript.removeEventListener('scroll', schedule); };
  }, [messages, active, transcriptRef]);
  useEffect(() => { setPreview(null); }, [messages.length, active]);
  if (prompts.length < 2) return null;
  function showPreview(prompt, button) {
    const bounds = button.getBoundingClientRect(), region = rail.current.parentElement.getBoundingClientRect();
    const index = messages.findIndex((message) => message.id === prompt.id);
    const response = messages[index + 1]?.role === 'assistant' ? messages[index + 1].message : '';
    setPreview({ id: prompt.id, prompt: prompt.message, response, top: Math.max(8, Math.min(bounds.top - region.top - 24, region.height - 140)) });
  }
  function jump(id) {
    const element = document.getElementById(`message-${id}`);
    if (!element) return;
    onNavigate?.();
    element.scrollIntoView({ block: 'start', behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
    element.focus({ preventScroll: true });
    setCurrent(id); setPreview(null);
  }
  return <>
    <nav ref={rail} className="prompt-navigation" aria-label="Conversation prompts" onMouseLeave={() => setPreview(null)}>
      {prompts.map((prompt, index) => <button key={prompt.id} type="button"
        className={current === prompt.id ? 'prompt-mark current' : 'prompt-mark'}
        aria-label={`Jump to prompt ${index + 1}: ${prompt.message.slice(0, 100)}`}
        aria-current={current === prompt.id ? 'location' : undefined}
        aria-controls={`message-${prompt.id}`}
        onMouseEnter={(event) => showPreview(prompt, event.currentTarget)}
        onFocus={(event) => showPreview(prompt, event.currentTarget)}
        onBlur={() => setPreview(null)}
        onKeyDown={(event) => { if (event.key === 'Escape') setPreview(null); }}
        onClick={() => jump(prompt.id)}><span aria-hidden="true" /></button>)}
    </nav>
    {preview && <div className="prompt-preview" aria-hidden="true" style={{ top: preview.top }}>
      <strong>{preview.prompt}</strong><p>{preview.response || 'No response yet'}</p>
    </div>}
  </>;
}
