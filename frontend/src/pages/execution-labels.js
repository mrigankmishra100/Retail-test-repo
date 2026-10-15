export const apiLabel = (source) => ({
  oci_responses: 'OCI Responses API', oci_chat: 'Native OCI Chat',
  openai_responses: 'OpenAI Responses API', gemini: 'Gemini API', anthropic: 'Anthropic API',
}[source] || 'Model API');

export const fileSearchLabel = (source) => source ? `${apiLabel(source)} · File Search` : 'Provider File Search';

export function controlDescription(event) {
  if (event.stage === 'built_in_tool') return `File Search is provider-executed${event.api_source ? ` through ${apiLabel(event.api_source)}` : ''}. It does not pass through this application's MCP boundary.`;
  if (!['mcp_safety', 'mcp_result'].includes(event.stage)) return null;
  if (event.status === 'passed') return event.stage === 'mcp_result'
    ? "Returned data matched the tool's response schema; this does not verify factual correctness."
    : 'The local MCP boundary checked the session, arguments, permissions and tool policy before execution. Controlled tools also require recorded approval. No hosted MCP Safety API was called.';
  return ({session_required: 'An authenticated, valid session is required.',
    invalid_arguments: 'The supplied arguments did not match the tool schema.',
    permission_denied: 'The session role or supplier scope did not permit this tool call.',
    human_approval_required: 'The workflow did not provide the required recorded human approval.',
    approval_evidence_unavailable: 'Required workflow approval evidence could not be verified.',
    local_policy_denied: 'The local tool policy denied execution.',
    policy_check_unavailable: 'The configured policy check could not complete. Execution was stopped.',
    invalid_tool_result: 'The service returned data that did not match the tool response schema.'
  })[event.error_code] || 'The MCP boundary check did not pass.';
}
