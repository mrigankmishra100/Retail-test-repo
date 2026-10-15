import { useState } from 'react';
import './AgentRegistry.css';
import api from '../api';
import { Badge, Empty, ErrorNotice, Icon, Loading, Panel, useAction, useResource } from '../ui';

const initialForm = { name: '', agent_type: 'CUSTOM', description: '', endpoint_url: '', active: false };
const invokeUrl = /^https:\/\/inference\.generativeai\.[a-z]+(?:-[a-z]+)+-\d+\.oci\.oraclecloud\.com\/\d{8}\/hostedApplications\/ocid1\.generativeaihostedapplication\.oc1\.[a-z0-9.-]+\/actions\/invoke\/?$/;

function registryError(error) {
  if (!error) return null;
  // The shared error component's 409 advice is specific to replenishment cases.
  return {
    traceId: error.traceId,
    message: error.status === 409
      ? 'Another agent of this type is active. Deactivate it before activating this agent.'
      : error.status === 503
        ? 'Agent registry is unavailable. Check the database connection and that migration 004 has been applied.'
        : error.status === 422
          ? 'Check the required fields and use a public HTTPS OCI Hosted Application invoke URL.'
          : error.message,
  };
}

export default function AgentRegistry() {
  const [form, setForm] = useState(initialForm);
  const [offset, setOffset] = useState(0);
  const [message, setMessage] = useState('');
  const agents = useResource(() => api.registryAgents(offset), [offset]);
  const action = useAction();
  const update = (field, value) => setForm((current) => ({ ...current, [field]: value }));

  async function register(event) {
    event.preventDefault();
    setMessage('');
    const result = await action.run('register-agent', () => api.registerAgent({
      ...form, name: form.name.trim(), description: form.description.trim(), endpoint_url: form.endpoint_url.trim(),
    }));
    if (result) {
      setForm(initialForm);
      setOffset(0);
      agents.reload();
      setMessage(`${result.name} registered.`);
    }
  }

  async function toggle(agent) {
    setMessage('');
    const result = await action.run(agent.agent_id, () => api.setAgentActive(agent.agent_id, !agent.active));
    if (result) {
      agents.reload();
      setMessage(`${result.name} ${result.active ? 'activated' : 'deactivated'}.`);
    }
  }

  return (
    <div className="agent-registry">
      {message && <div className="callout info" role="status">{message}</div>}
      <ErrorNotice error={registryError(action.error)} />
      <Panel title="Register an agent" className="registry-registration">
        <form className="registry-form" onSubmit={register}>
          <fieldset disabled={action.busy}>
            <legend className="sr-only">Agent details</legend>
            <div className="registry-fields">
              <label>
                Name
                <input required maxLength={120} value={form.name} onChange={(e) => update('name', e.target.value)} />
              </label>
              <label>
                Role / type
                <select value={form.agent_type} onChange={(e) => update('agent_type', e.target.value)}>
                  <option value="RETAIL">Retail</option>
                  <option value="SUPPLIER">Supplier</option>
                  <option value="CUSTOM">Custom</option>
                </select>
              </label>
              <label>
                Status
                <select value={String(form.active)} onChange={(e) => update('active', e.target.value === 'true')}>
                  <option value="false">Inactive</option>
                  <option value="true">Active</option>
                </select>
              </label>
            </div>
            <label>
              Public OCI Hosted Application URL
              <input
                type="url" required maxLength={2000} value={form.endpoint_url}
                aria-describedby="registry-url-help"
                onChange={(e) => {
                  const value = e.target.value;
                  e.target.setCustomValidity(!value || invokeUrl.test(value.trim())
                    ? '' : 'Enter an HTTPS OCI Hosted Application URL ending in /actions/invoke.');
                  update('endpoint_url', value);
                }}
              />
            </label>
            <p id="registry-url-help" className="registry-help">
              HTTPS invoke URL ending in /actions/invoke.
            </p>
            <details className="registry-optional"><summary>Add a description</summary>
              <label>Description<textarea rows={2} maxLength={1000} value={form.description} onChange={(e) => update('description', e.target.value)} /></label>
            </details>
            <button className="primary" disabled={action.busy || !form.name.trim()}>
              {action.busy ? 'Saving...' : 'Register agent'}
            </button>
          </fieldset>
        </form>
      </Panel>
      <Panel title="Registered agents" className="registry-directory"
        action={<button disabled={agents.loading || action.busy} onClick={agents.reload}><Icon name="refresh" size={16} /> Refresh</button>}>
        <ErrorNotice error={registryError(agents.error)} retry={agents.reload} />
        {agents.loading ? <Loading label="Loading registered agents..." /> : agents.data?.items.length ? (
          <div className="table-scroll">
            <table><colgroup><col style={{width:'40%'}} /><col style={{width:'18%'}} /><col style={{width:'17%'}} /><col style={{width:'25%'}} /></colgroup>
              <thead><tr><th>Agent</th><th>Type</th><th>Status</th><th>Action</th></tr></thead>
              <tbody>
                {agents.data.items.map((agent) => (
                  <tr key={agent.agent_id}>
                    <td className="registry-description"><strong>{agent.name}</strong>
                      <details className="registry-agent-details"><summary aria-label={`Details for ${agent.name}`}>Details</summary>
                        {agent.description && <p>{agent.description}</p>}
                        <span>Hosted application URL</span><code>{agent.endpoint_url}</code>
                      </details>
                    </td>
                    <td><Badge>{agent.agent_type}</Badge>{agent.agent_type === 'CUSTOM' && <small>Stored only</small>}</td>
                    <td><Badge tone={agent.active ? 'success' : 'neutral'}>{agent.active ? 'Active' : 'Inactive'}</Badge></td>
                    <td><button disabled={action.busy} onClick={() => toggle(agent)} aria-label={`${agent.active ? 'Deactivate' : 'Activate'} ${agent.name}`}>
                      {agent.active ? 'Deactivate' : 'Activate'}
                    </button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : !agents.error && <Empty title="No registered agents">Register an agent above to add it to this directory.</Empty>}
        {(offset > 0 || agents.data?.has_more) && (
          <div className="registry-pagination">
            <button disabled={offset === 0 || agents.loading || action.busy} onClick={() => setOffset((n) => n - 20)}>Previous</button>
            <span>Page {offset / 20 + 1}</span>
            <button disabled={!agents.data?.has_more || agents.loading || action.busy || offset >= 10000} onClick={() => setOffset((n) => n + 20)}>Next</button>
          </div>
        )}
      </Panel>
      <details className="registry-guidance"><summary>Registration and routing</summary>
        <p>{agents.data?.enforcement_enabled
          ? 'Execution control is on: deactivating the last active Retail or Supplier entry blocks new executions for that role. Roles without entries use their existing configuration.'
          : 'Execution control is off; inactive entries do not block local agents.'}</p>
        <p>One active agent per Retail or Supplier role. Remote execution requires an authenticated worker using the application contract. Custom entries are directory only.</p>
      </details>
    </div>
  );
}
