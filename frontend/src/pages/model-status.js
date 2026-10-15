export const outcome = (metrics) => metrics?.status === 'completed' && metrics.calls?.some((c) => c.status === 'failed') ? 'ai_unavailable' : metrics?.status;
export const needsAttention = (metrics) => ['failed', 'disabled', 'limit_reached', 'blocked', 'unavailable', 'ai_unavailable'].includes(outcome(metrics));
export const modelError = (call) => ({
  external_model_not_found: 'The selected model is unavailable for this provider key. Reconnect and load available models.',
  external_rate_limit: 'The external provider quota or rate limit was reached.',
  external_authentication_failed: 'The external provider rejected the API key or its permissions.',
  native_tools_require_responses: 'This model rejects tools with reasoning on native Chat. Use Responses API with its Responses model ID.',
}[call.error_code] || (call.http_status === 404 && call.error_code?.startsWith('external_')
  ? 'The selected model is unavailable for this provider key. Reconnect and load available models.'
  : call.error_code?.replaceAll('_', ' ')));
