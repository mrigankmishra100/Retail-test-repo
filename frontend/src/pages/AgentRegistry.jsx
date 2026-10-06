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
      <div className="callout info">
        <Icon name="flow" />
        <div>
          Active Retail and Supplier entries select the destination for configured remote agents.
          When no entry is active, the existing configured URL is used. Only one Retail and one
          Supplier entry can be active. Custom agents are listed here only and are not invoked.
        </div>
      </div>
      {message && <div className="callout info" role="status">{message}</div>}
      <ErrorNotice error={registryError(action.error)} />
      <Panel title="Register an agent" subtitle="Add a public OCI Hosted Application to the shared registry.">
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
            </div>
            <label>
              Description
              <textarea rows={2} maxLength={1000} value={form.description} onChange={(e) => update('description', e.target.value)} />
            </label>
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
              Use the HTTPS invoke URL ending in /actions/invoke, without query parameters or an
              extra path. Retail and Supplier agents must support the existing application contract
              and service key for their role.
            </p>
            <label>
              Status
              <select value={String(form.active)} onChange={(e) => update('active', e.target.value === 'true')}>
                <option value="false">Inactive</option>
                <option value="true">Active</option>
              </select>
            </label>
            <button className="primary" disabled={action.busy || !form.name.trim()}>
              {action.busy ? 'Saving...' : 'Register agent'}
            </button>
          </fieldset>
        </form>
      </Panel>
      <Panel title="Registered agents" subtitle="Shared by Retail managers and Suppliers."
        action={<button disabled={agents.loading || action.busy} onClick={agents.reload}><Icon name="refresh" size={16} /> Refresh</button>}>
        <ErrorNotice error={registryError(agents.error)} retry={agents.reload} />
        {agents.loading ? <Loading label="Loading registered agents..." /> : agents.data?.items.length ? (
          <div className="table-scroll">
            <table>
              <thead><tr><th>Agent</th><th>Type</th><th>Hosted application URL</th><th>Status</th><th>Action</th></tr></thead>
              <tbody>
                {agents.data.items.map((agent) => (
                  <tr key={agent.agent_id}>
                    <td className="registry-description"><strong>{agent.name}</strong><small>{agent.description || 'No description'}</small></td>
                    <td><Badge>{agent.agent_type}</Badge>{agent.agent_type === 'CUSTOM' && <small>Stored only</small>}</td>
                    <td className="registry-url">{agent.endpoint_url}</td>
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
    </div>
  );
}
