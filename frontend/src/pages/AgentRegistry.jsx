import { useState } from 'react';
import './AgentRegistry.css';
import api from '../api';
import { Badge, Dialog, Empty, ErrorNotice, Icon, Loading, Panel, useAction, useResource } from '../ui';

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
        ? 'Agent registry is unavailable. Check the database connection and registry migrations (004; also 005 for Delivery if the automatic upgrade is not permitted).'
        : error.status === 422
          ? 'Check the required fields, endpoint format and API token if connecting a Delivery agent.'
          : error.message,
  };
}

function DeliveryConnection({ agent, connection, onMessage }) {
  const [token, setToken] = useState('');
  const action = useAction();
  const selected = connection.data?.agent_id === agent.agent_id;
  const saved = selected && connection.data?.connected;
  const available = selected && connection.data?.available;
  const statusText = connection.loading ? 'Checking connection…'
    : connection.error ? 'Connection status unavailable'
    : available ? 'Connected for this session'
    : saved ? 'Connection saved — activate this agent'
    : selected ? 'Reconnect required'
    : 'Not connected for this session';
  const failure = action.error ? {
    traceId: action.error.traceId,
    message: action.error.status === 422 ? 'Token was not saved. Paste the token value or Bearer followed by the token, without Authorization: or extra lines.'
      : action.error.status === 404 || action.error.status === 405 ? 'Delivery connection route is unavailable. Deploy matching frontend and backend images with the latest API routes.'
      : action.error.status === 503 ? 'Connection could not be updated. Check the backend and connection storage, then refresh the status.'
      : action.error.message,
  } : null;
  return <div>
    <p role="status"><Badge tone={available ? 'success' : 'neutral'}>{statusText}</Badge></p>
    <details><summary>Connect delivery API</summary>
    <p>Use this agent for delivery questions in this supplier session. Its API must accept a question and return the distributor answer format. Connecting replaces this session's previous delivery connection. The token expires with the session and is never included in chat.</p>
    {saved && <p>The connection is saved on the server; saved tokens are never displayed here. API authorization is checked when you send a delivery question.</p>}
    <ErrorNotice error={failure} />
    <ErrorNotice error={connection.error && { message: 'Could not read the saved connection. Check deployed frontend/backend versions, API routing and connection storage.', traceId: connection.error.traceId }} retry={connection.reload} />
    <form onSubmit={async (event) => {
      event.preventDefault();
      const result = await action.run(`connect-${agent.agent_id}`, () => api.connectDeliveryAgent(agent.agent_id, token));
      setToken('');
      connection.reload();
      if (result) onMessage(`Delivery connection saved for ${agent.name} in this session. Ask a delivery question in supplier chat to call this agent.`);
    }}>
      <label>Delivery API token (optional)<input type="password" autoComplete="off" minLength={16} maxLength={4105}
        value={token} onChange={(e) => setToken(e.target.value)} /></label>
      <p>Leave blank only if this API needs no authentication. Connecting with a blank field replaces any saved token with an unauthenticated connection.</p>
      <button disabled={action.busy}>Connect for this session</button>
      <button type="button" disabled={action.busy} onClick={async () => {
        const result = await action.run(`disconnect-${agent.agent_id}`, () => api.disconnectDeliveryAgent(agent.agent_id));
        setToken('');
        connection.reload();
        if (result) onMessage('Delivery connection removed from this session.');
      }}>Disconnect</button>
      <button type="button" disabled={action.busy || connection.loading} onClick={connection.reload}>Refresh connection status</button>
    </form>
  </details></div>;
}

export default function AgentRegistry({ session }) {
  const [form, setForm] = useState(initialForm);
  const [connectOnRegister, setConnectOnRegister] = useState(false);
  const [registrationToken, setRegistrationToken] = useState('');
  const registrationConnection = useAction();
  const [offset, setOffset] = useState(0);
  const [message, setMessage] = useState('');
  const [deleting, setDeleting] = useState(null);
  const deletion = useAction();
  const agents = useResource(() => api.registryAgents(offset), [offset]);
  const connection = useResource(() => session?.role === 'supplier'
    ? api.deliveryConnectionStatus() : Promise.resolve(null), [session?.session_id, agents.data], session?.role === 'supplier' ? 30000 : 0);
  const action = useAction();
  const registering = action.busy || registrationConnection.busy;
  const update = (field, value) => setForm((current) => ({ ...current, [field]: value }));

  async function register(event) {
    event.preventDefault();
    setMessage('');
    registrationConnection.clear();
    const connect = session?.role === 'supplier' && form.agent_type === 'CUSTOM' && connectOnRegister;
    const token = registrationToken;
    const result = await action.run('register-agent', () => api.registerAgent({
      ...form, name: form.name.trim(), description: form.description.trim(), endpoint_url: form.endpoint_url.trim(),
    }));
    if (result) {
      // Registration and private session credentials use separate endpoints. A
      // connection failure leaves the new row available to retry without duplicates.
      setForm(initialForm);
      setRegistrationToken('');
      setConnectOnRegister(false);
      setOffset(0);
      agents.reload();
      if (connect) {
        const saved = await registrationConnection.run('connect-new-agent', () => api.connectDeliveryAgent(result.agent_id, token));
        connection.reload();
        setMessage(saved ? `${result.name} registered. ${result.active ? 'Connected for delivery questions in this session.' : 'Connection saved; activate this agent to use it.'}`
          : `${result.name} registered, but its delivery connection was not saved. Use Connect delivery API on this entry to retry; do not register it again.`);
      } else setMessage(`${result.name} registered.`);
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
      {deleting && <Dialog title={`Delete ${deleting.name}?`} onClose={() => { if (!deletion.busy) setDeleting(null); }}>
        <p>This removes the registry entry. It does not delete the hosted application or existing chats. Delivery connections using this entry will stop working.</p>
        {['RETAIL', 'SUPPLIER'].includes(deleting.agent_type) && <p>Deployment bootstrap may register this role again when the backend restarts. Keep it deactivated instead if you want it to remain disabled.</p>}
        <ErrorNotice error={deletion.error && { traceId: deletion.error.traceId,
          message: deletion.error.status === 409 ? 'This agent is active. Close this dialog, refresh and deactivate it before deleting.'
            : deletion.error.status === 404 ? 'This entry is already missing. Close this dialog and refresh the registry.'
            : 'Could not delete the entry. Check the backend connection and try again.' }} />
        <div className="registry-actions">
          <button disabled={deletion.busy} onClick={() => setDeleting(null)}>Cancel</button>
          <button className="primary" disabled={deletion.busy} onClick={async () => {
            const result = await deletion.run('delete', () => api.deleteAgent(deleting.agent_id));
            if (result?.deleted) {
              setMessage(`${deleting.name} removed from the registry.`);
              setDeleting(null);
              if (offset > 0 && agents.data?.items.length === 1) setOffset(offset - 20);
              agents.reload();
              connection.reload();
            }
          }}>{deletion.busy ? 'Deleting…' : 'Delete registration'}</button>
        </div>
      </Dialog>}
      {message && <div className="callout info" role="status">{message}</div>}
      <ErrorNotice error={registryError(action.error)} />
      <ErrorNotice error={registrationConnection.error && { traceId: registrationConnection.error.traceId,
        message: 'The agent was registered, but connecting it failed. Check the token format and backend connection storage, then retry on the registered entry.' }} />
      <Panel title="Register an agent" className="registry-registration">
        <form className="registry-form" onSubmit={register}>
          <fieldset disabled={registering}>
            <legend className="sr-only">Agent details</legend>
            <div className="registry-fields">
              <label>
                Name
                <input required maxLength={120} value={form.name} onChange={(e) => update('name', e.target.value)} />
              </label>
              <label>
                Role / type
                <select value={form.agent_type} onChange={(e) => { update('agent_type', e.target.value); setRegistrationToken(''); setConnectOnRegister(false); }}>
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
              {form.agent_type === 'CUSTOM' ? 'Custom agent endpoint' : 'Public OCI Hosted Application URL'}
              <input
                key={form.agent_type}
                type="url" required maxLength={2000} value={form.endpoint_url}
                aria-describedby="registry-url-help"
                onChange={(e) => {
                  const value = e.target.value;
                  const valid = form.agent_type === 'CUSTOM'
                    ? invokeUrl.test(value.trim()) || /^https:\/\/(?:[a-z0-9.-]+|\[[a-f0-9:]+\])(?::[0-9]{1,5})?\/[A-Za-z0-9/_-]+$/.test(value.trim()) : invokeUrl.test(value.trim());
                  e.target.setCustomValidity(!value || valid
                    ? '' : form.agent_type === 'CUSTOM' ? 'Enter the full public HTTPS API endpoint, including its port and path.' : 'Enter an HTTPS OCI Hosted Application URL ending in /actions/invoke.');
                  update('endpoint_url', value);
                }}
              />
            </label>
            <p id="registry-url-help" className="registry-help">
              {form.agent_type === 'CUSTOM' ? 'Use any display name and the full public HTTPS API endpoint, including its port. Select delivery questions below to connect a compatible distributor question/answer API during registration.' : 'HTTPS invoke URL ending in /actions/invoke.'}
            </p>
            {form.agent_type === 'CUSTOM' && session?.role === 'supplier' && <div className="registry-optional">
              <label className="checkbox"><input type="checkbox" checked={connectOnRegister}
                onChange={(e) => { setConnectOnRegister(e.target.checked); if (!e.target.checked) setRegistrationToken(''); }} />Use for delivery questions in this session</label>
              {connectOnRegister && <>
                <label>Bearer token (optional)<input type="password" autoComplete="off" minLength={16} maxLength={4105}
                  value={registrationToken} onChange={(e) => setRegistrationToken(e.target.value)} /></label>
                <p className="registry-help">Paste the token value or Bearer followed by the token. Leave blank only if the API allows unauthenticated requests. Registering also saves this session's delivery connection, replacing any previous one.</p>
              </>}
            </div>}
            {form.agent_type === 'CUSTOM' && session?.role !== 'supplier' && <p className="registry-help">Sign in as a supplier to connect this API for delivery questions.</p>}
            <details className="registry-optional"><summary>Add a description</summary>
              <label>Description<textarea rows={2} maxLength={1000} value={form.description} onChange={(e) => update('description', e.target.value)} /></label>
            </details>
            <button className="primary" disabled={registering || !form.name.trim()}>
              {registering ? 'Saving...' : 'Register agent'}
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
                        <span>Endpoint URL</span><code>{agent.endpoint_url}</code>
                      </details>
                      {['CUSTOM', 'DELIVERY'].includes(agent.agent_type) && (session?.role === 'supplier'
                        ? <DeliveryConnection agent={agent} connection={connection} onMessage={setMessage} />
                        : <small>Sign in as supplier to connect the delivery API for that session.</small>)}
                    </td>
                    <td><Badge>{agent.agent_type}</Badge>{agent.agent_type === 'CUSTOM' && <small>Optional delivery connection</small>}</td>
                    <td><Badge tone={agent.active ? 'success' : 'neutral'}>{agent.active ? 'Active' : 'Inactive'}</Badge></td>
                    <td><div className="registry-actions"><button disabled={action.busy} onClick={() => toggle(agent)} aria-label={`${agent.active ? 'Deactivate' : 'Activate'} ${agent.name}`}>
                      {agent.active ? 'Deactivate' : 'Activate'}
                    </button><button disabled={action.busy || agent.active} title={agent.active ? 'Deactivate before deleting' : 'Remove this registry entry'} aria-label={`Delete ${agent.name}`} onClick={() => { deletion.clear(); setDeleting(agent); }}>Delete</button></div></td>
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
        <p>One active agent per Retail or Supplier role. Custom entries stay in the directory until you connect a compatible distributor API for supplier chat. One delivery connection per supplier session; names do not determine routing. Existing Delivery entries remain supported.</p>
      </details>
    </div>
  );
}
