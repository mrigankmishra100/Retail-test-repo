export const apiLabel = (source) => ({
  oci_responses: 'OCI Responses API', oci_chat: 'Native OCI Chat',
  openai_responses: 'OpenAI Responses API', gemini: 'Gemini API', anthropic: 'Anthropic API',
}[source] || 'Model API');
