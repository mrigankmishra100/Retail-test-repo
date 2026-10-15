export const regionalId = 'regional-evaluation';
export const initialRegionalModel = { label: 'Regional model', region: 'us-chicago-1', model_id: '', api_mode: 'responses', auth_mode: 'genai_api_key', compartment_id: '', max_completion_tokens: 2048, project_id: '', api_key: '', vector_store_ids: '' };

export default function RegionalModelForm({ enabled, toggle, value, onChange, selected, available }) {
  const field = (key, next) => onChange((current) => ({ ...current, [key]: next }));
  const native = value.api_mode === 'native_chat';
  return <details className="regional-adapter">
    <summary>Compare a model in another OCI region</summary>
    <label className="eval-rag"><input type="checkbox" checked={enabled} disabled={!available} onChange={(event) => toggle(event.target.checked)} /> Enable regional comparison model</label>
    {!available && <p>Available when the retail agent executes locally.</p>}
    {enabled && <>
      <div className="regional-adapter-grid">
        <label>API type<select aria-label="API type" value={value.api_mode} onChange={(e) => onChange((current) => ({ ...current, api_mode: e.target.value, model_id: '', api_key: '', vector_store_ids: '' }))}><option value="responses">Responses API</option><option value="native_chat">Native OCI Chat · DEFAULT profile</option></select></label>
        <label>Display name<input maxLength="100" required={selected} value={value.label} onChange={(e) => field('label', e.target.value)} /></label>
        <label>OCI region<input required={selected} pattern="[a-z]+(-[a-z]+)+-[0-9]+" maxLength="60" value={value.region} onChange={(e) => field('region', e.target.value)} placeholder="us-chicago-1" /></label>
        <label>Model ID<input required={selected} maxLength="256" value={value.model_id} onChange={(e) => field('model_id', e.target.value)} placeholder={native ? 'ocid1.generativeaimodel.oc1..' : 'OCI Responses model ID'} /></label>
        {native ? <>
          <label>Compartment OCID<input required={selected} maxLength="256" value={value.compartment_id} onChange={(e) => field('compartment_id', e.target.value)} placeholder="ocid1.compartment.oc1.." /></label>
          <label>Maximum completion tokens<input type="number" min="256" max="6000" required={selected} value={value.max_completion_tokens} onChange={(e) => field('max_completion_tokens', e.target.value === '' ? '' : Number(e.target.value))} /></label>
        </> : <>
        <label>Authentication<select aria-label="Authentication" value={value.auth_mode} onChange={(e) => onChange((current) => ({ ...current, auth_mode: e.target.value, api_key: '' }))}><option value="genai_api_key">GenAI API key</option><option value="oci">OCI DEFAULT profile</option></select></label>
        <label>Project OCID<input required={selected} maxLength="256" value={value.project_id} onChange={(e) => field('project_id', e.target.value)} placeholder="Project in the selected region" /></label>
        {value.auth_mode === 'genai_api_key' && <label>OCI GenAI API key<input type="password" required={selected} maxLength="4096" autoComplete="off" value={value.api_key} onChange={(e) => field('api_key', e.target.value)} placeholder="Key authorized for the target project" /></label>}
        <label>Vector store IDs (optional)<input maxLength="1280" value={value.vector_store_ids} onChange={(e) => field('vector_store_ids', e.target.value)} placeholder="Comma-separated IDs in the target project" /></label>
        </>}
      </div>
      <details className="regional-connection-details"><summary>Connection details</summary>
      <p>Prompts and tool results are sent to the selected region.</p>
      {native || value.auth_mode === 'oci' ? <p>Uses the server's DEFAULT OCI profile.{native && ' Native Chat has no built-in File Search; model tool support varies.'}</p> : <p>The key is cleared after submission and is not saved. Use localhost or trusted HTTPS.</p>}
      <small>Endpoint: <code>https://inference.generativeai.{value.region || '[region]'}.oci.oraclecloud.com{native ? '' : '/openai/v1'}</code></small>
      </details>
    </>}
  </details>;
}
