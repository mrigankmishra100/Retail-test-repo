import { useEffect, useState } from 'react';
import api from '../api';
import { Dialog, ErrorNotice } from '../ui';

const providers = {
  gemini: { name: 'Google Gemini', keyUrl: 'https://aistudio.google.com/apikey', model: '' },
  openai: { name: 'OpenAI', keyUrl: 'https://platform.openai.com/api-keys', model: '' },
  anthropic: { name: 'Anthropic', keyUrl: 'https://console.anthropic.com/settings/keys', model: '' },
};

export default function ExternalModelDialog({ onClose, onConnected, purpose = 'workflow', serverOpenAI = false }) {
  const [form, setForm] = useState({ provider: serverOpenAI ? 'openai' : 'gemini', model_id: '', api_key: '' });
  const [busy, setBusy] = useState(false), [error, setError] = useState(null);
  const [models, setModels] = useState([]);
  const [useServerKey, setUseServerKey] = useState(serverOpenAI);
  const update = (key, value) => setForm((current) => ({ ...current, [key]: value }));
  useEffect(() => { if (serverOpenAI) loadModels(); }, [serverOpenAI]);
  const credentials = () => useServerKey ? { provider: 'openai', use_server_key: true } : { provider: form.provider, api_key: form.api_key };
  function handleError(error) {
    if (useServerKey && error.data?.error?.code === 'openai_not_configured') {
      setUseServerKey(false);
      setModels([]);
      setForm((current) => ({ ...current, model_id: '', api_key: '' }));
      setError(null);
    } else setError(error);
  }
  async function loadModels() {
    setBusy('catalog'); setError(null);
    try {
      const result = await api.externalModelOptions(credentials());
      setModels(result.models);
      if (!result.models.includes(form.model_id)) update('model_id', '');
      if (!result.models.length) setError({ message: 'No available models were returned for this key.' });
    } catch (e) { handleError(e); setModels([]); update('model_id', ''); }
    finally { setBusy(false); }
  }
  async function connect(event) {
    event.preventDefault(); setBusy(true); setError(null);
    try {
      const result = await api.connectExternalModel({ ...credentials(), max_tokens: form.provider === 'openai' ? null : 4096, model_id: form.model_id.trim() });
      setForm((value) => ({ ...value, api_key: '' }));
      if (await onConnected(result.profile)) onClose();
      else setError({ message: 'The model was added, but the workflow selection could not be updated. Close this form, review the workflow error, and choose the model again.' });
    } catch (e) { handleError(e); setForm((value) => ({ ...value, api_key: '' })); }
    finally { setBusy(false); }
  }
  return <Dialog centered title={serverOpenAI ? "Choose an OpenAI model" : "Use an external model"} onClose={() => { if (!busy) onClose(); }}>
    <form className="external-model-form" onSubmit={connect} autoComplete="off">
      <p>{purpose === 'comparison' ? 'Choose a model to compare.' : 'Choose a model for retail chat and workflow.'}</p>
      <fieldset disabled={busy}>
        {!serverOpenAI && <label>Provider<select aria-label="Provider" value={form.provider} onChange={(event) => { setModels([]); setError(null); setForm({ provider: event.target.value, model_id: providers[event.target.value].model, api_key: '' }); }}>
          {Object.entries(providers).map(([id, provider]) => <option key={id} value={id}>{provider.name}</option>)}
        </select></label>}
        {!useServerKey && <>
        {serverOpenAI && <small>No shared OpenAI key is configured. Enter your API key to use OpenAI for this session.</small>}
        <label>API key<input required type="password" autoComplete="new-password" minLength={12} maxLength={512} value={form.api_key} onChange={(e) => { setForm((current) => ({ ...current, api_key: e.target.value, model_id: models.length ? '' : current.model_id })); setModels([]); setError(null); }} /></label>
        <a href={providers[form.provider].keyUrl} target="_blank" rel="noreferrer">Get a {providers[form.provider].name} API key ↗</a></>}
        {!useServerKey && <div className="external-model-discovery"><button type="button" disabled={form.api_key.length < 12} onClick={loadModels}>Load available models</button></div>}
        <label>Model ID{serverOpenAI || models.length > 0 ? <select aria-label="Model ID" required value={form.model_id} onChange={(e) => update('model_id', e.target.value)}>
          <option value="">{busy === 'catalog' ? 'Loading models…' : 'Choose an available model'}</option>{models.map((id) => <option key={id} value={id}>{id}</option>)}
        </select> : <input required maxLength={150} pattern="[A-Za-z0-9][A-Za-z0-9._:\-]*" value={form.model_id} onChange={(e) => update('model_id', e.target.value)} placeholder="Load models above or enter an exact model ID" spellCheck="false" />}</label>
        <div className="model-picker-help"><small>Choose a text model with function calling.</small>{useServerKey && <button type="button" onClick={loadModels}>Refresh models</button>}</div>
      </fieldset>
      {!useServerKey && <small>Your key is held on the server for this session. Your provider processes prompts and tool data.</small>}
      <ErrorNotice error={error} />
      <div className="external-model-actions"><button type="button" disabled={busy} onClick={onClose}>Cancel</button><button className="primary" disabled={busy}>{busy === 'catalog' ? 'Loading models…' : busy ? 'Connecting…' : purpose === 'comparison' ? 'Add to comparison' : 'Use model'}</button></div>
    </form>
  </Dialog>;
}
