import { useState } from 'react';
import api from '../api';
import { Dialog, ErrorNotice } from '../ui';

const providers = {
  gemini: { name: 'Google Gemini', keyUrl: 'https://aistudio.google.com/apikey', model: '' },
  openai: { name: 'OpenAI', keyUrl: 'https://platform.openai.com/api-keys', model: '' },
  anthropic: { name: 'Anthropic', keyUrl: 'https://console.anthropic.com/settings/keys', model: '' },
};

export default function ExternalModelDialog({ onClose, onConnected, purpose = 'workflow' }) {
  const [form, setForm] = useState({ provider: 'gemini', model_id: providers.gemini.model, api_key: '', max_tokens: 4096 });
  const [busy, setBusy] = useState(false), [error, setError] = useState(null);
  const [models, setModels] = useState([]);
  const update = (key, value) => setForm((current) => ({ ...current, [key]: value }));
  async function loadModels() {
    setBusy('catalog'); setError(null);
    try {
      const result = await api.externalModelOptions({ provider: form.provider, api_key: form.api_key });
      setModels(result.models);
      if (!result.models.includes(form.model_id)) update('model_id', '');
      if (!result.models.length) setError({ message: 'No available models were returned for this key.' });
    } catch (e) { setError(e); setModels([]); update('model_id', ''); }
    finally { setBusy(false); }
  }
  async function connect(event) {
    event.preventDefault(); setBusy(true); setError(null);
    try {
      const result = await api.connectExternalModel({ ...form, model_id: form.model_id.trim() });
      setForm((value) => ({ ...value, api_key: '' }));
      if (await onConnected(result.profile)) onClose();
      else setError({ message: 'The model was added, but the workflow selection could not be updated. Close this form, review the workflow error, and choose the model again.' });
    } catch (e) { setError(e); setForm((value) => ({ ...value, api_key: '' })); }
    finally { setBusy(false); }
  }
  return <Dialog title="Use an external model" onClose={() => { if (!busy) onClose(); }}>
    <form className="external-model-form" onSubmit={connect} autoComplete="off">
      <p>{purpose === 'comparison' ? 'Compare this model using the same prompt and read-only tools.' : 'Use your provider for chat and remaining workflow AI steps.'} Prompts and tool data are sent to that provider.</p>
      <fieldset disabled={busy}>
        <label>Provider<select aria-label="Provider" value={form.provider} onChange={(event) => { setModels([]); setError(null); setForm({ provider: event.target.value, model_id: providers[event.target.value].model, api_key: '', max_tokens: 4096 }); }}>
          {Object.entries(providers).map(([id, provider]) => <option key={id} value={id}>{provider.name}</option>)}
        </select></label>
        <label>API key<input required type="password" autoComplete="new-password" minLength={12} maxLength={512} value={form.api_key} onChange={(e) => { setForm((current) => ({ ...current, api_key: e.target.value, model_id: models.length ? '' : current.model_id })); setModels([]); setError(null); }} /></label>
        <a href={providers[form.provider].keyUrl} target="_blank" rel="noreferrer">Get a {providers[form.provider].name} API key ↗</a>
        <div className="external-model-discovery"><button type="button" disabled={form.api_key.length < 12} onClick={loadModels}>Load available models</button><small>Lists models returned for this key. Choose a model that supports function calling; quota, credits and free-tier eligibility are separate.</small></div>
        <label>Model ID{models.length > 0 ? <select aria-label="Model ID" required value={form.model_id} onChange={(e) => update('model_id', e.target.value)}>
          <option value="">Choose an available model</option>{models.map((id) => <option key={id} value={id}>{id}</option>)}
        </select> : <input required maxLength={150} pattern="[A-Za-z0-9][A-Za-z0-9._:\-]*" value={form.model_id} onChange={(e) => update('model_id', e.target.value)} placeholder="Load models above or enter an exact model ID" spellCheck="false" />}</label>
        <details><summary>Output limit</summary><label>Maximum output tokens<input type="number" required min={256} max={16000} value={form.max_tokens} onChange={(e) => update('max_tokens', Number(e.target.value))} /></label></details>
      </fieldset>
      <small>Session only. Reconnect after a server restart. Policy tools remain available; OCI File Search is unavailable with external models.</small>
      <ErrorNotice error={error} />
      <div className="external-model-actions"><button type="button" disabled={busy} onClick={onClose}>Cancel</button><button className="primary" disabled={busy}>{busy === 'catalog' ? 'Loading models…' : busy ? 'Connecting…' : purpose === 'comparison' ? 'Add to comparison' : 'Use model'}</button></div>
    </form>
  </Dialog>;
}
