import { Icon } from '../ui';
import './execution-details.css';

const reasons = {
  prompt_injection_detected: 'OCI detected an attempt to override the agent’s instructions.',
  credential_material_detected: 'A local rule detected credential-like material in the content.',
  security_check_unavailable: 'The security check could not be completed. Processing stopped because no valid clearance was available.',
  action_requires_approval: 'This action requires the dedicated workflow controls and explicit human approval.',
  unverified_action_claim: 'The generated answer claimed an action without verified workflow evidence.',
  checks_passed: 'The configured security check passed.',
};
const stages = { input: 'User prompt', history: 'Conversation history', tool_arguments: 'Tool arguments',
  retrieval: 'Retrieved evidence', output: 'Generated answer', action: 'Workflow action' };

export default function SecurityDecision({ details, status }) {
  const state = details?.status || status;
  if (!['BLOCKED', 'FAILED'].includes(state)) return null;
  const local = details?.provider === 'local';
  const provider = local ? 'Local application rule' : details?.provider === 'oci' ? 'OCI ApplyGuardrails' : 'Application security';
  return <section className={`security-decision ${state.toLowerCase()}`} aria-label="Guardrails decision">
    <div className="security-decision-heading"><Icon name={state === 'FAILED' ? 'alert' : 'shield'} size={18} />
      <strong>{state === 'FAILED' ? 'Guardrails failed' : 'Guardrails blocked'}</strong><span>{provider}</span></div>
    <p>{reasons[details?.reason_code] || (state === 'FAILED' ? reasons.security_check_unavailable : 'The request or response did not pass the application’s security policy.')}</p>
    <details><summary>View security details</summary>
      {details ? <>
        <dl className="execution-detail-grid">
          <div><dt>Decision source</dt><dd>{provider}</dd></div>
          <div><dt>Checked at</dt><dd>{stages[details.stage] || details.stage}</dd></div>
          <div><dt>Reason</dt><dd>{details.reason_code.replaceAll('_', ' ')}</dd></div>
          <div><dt>OCI API request</dt><dd>{details.api_called ? 'Attempted' : 'Not called for this decision'}</dd></div>
          {typeof details.prompt_injection_score === 'number' && <div><dt>Prompt injection score</dt><dd>{details.prompt_injection_score} / 1</dd></div>}
          {details.version && <div><dt>Guardrails version</dt><dd>{details.version}</dd></div>}
          {typeof details.duration_ms === 'number' && <div><dt>Check duration</dt><dd>{details.duration_ms.toFixed(0)} ms</dd></div>}
          {details.http_status && <div><dt>OCI HTTP status</dt><dd>{details.http_status}</dd></div>}
        </dl>
        {local && <p className="execution-detail-note">This decision was enforced by local code; it was not an OCI detection result.</p>}
      </> : <p>Detailed decision metadata was not retained for this response.</p>}
    </details>
  </section>;
}
