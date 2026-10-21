import json
import oci
import requests
from app.distributor_rag.domain.models import Generation
from app.distributor_rag.domain.errors import ProviderError
from app.distributor_rag.providers.oci.common import oci_operation, serving

class OCILLMProvider:
    def __init__(self, client, settings, identity, signer=None):
        self.client, self.settings, self.identity = client, settings, identity
        self.signer = signer

    def generate_response(self, system, user_message, context, config):
        s = self.settings
        prompt = json.dumps({"question": user_message, "retrieved_sources": context}, ensure_ascii=False)
        with oci_operation("generation"):
            if s.oci_llm_format in {"responses", "chat_completions"}:
                text = self._compatible(system, prompt, config)
            else:
                models = oci.generative_ai_inference.models
                if s.oci_llm_format == "cohere":
                    request = models.CohereChatRequest(message=prompt, preamble_override=system,
                        max_tokens=config.max_tokens, temperature=config.temperature, is_stream=False)
                else:
                    parameters = (
                        {"max_completion_tokens": config.max_tokens, "verbosity": "MEDIUM"}
                        if s.oci_llm_format == "generic_astra"
                        else {"max_tokens": config.max_tokens, "temperature": config.temperature}
                    )
                    request = models.GenericChatRequest(messages=[
                        models.SystemMessage(content=[models.TextContent(text=system)]),
                        models.UserMessage(content=[models.TextContent(text=prompt)])],
                        is_stream=False, **parameters)
                result = self.client.chat(models.ChatDetails(compartment_id=s.oci_compartment_id,
                    serving_mode=serving(s.oci_llm_model_id, s.oci_llm_serving_mode), chat_request=request)).data.chat_response
                if s.oci_llm_format == "cohere":
                    text = result.text
                else:
                    if not result.choices or result.choices[0].finish_reason in {"length", "content_filter"}:
                        raise ProviderError("The OCI model response was incomplete.")
                    text = "".join(getattr(part, "text", "") or "" for part in result.choices[0].message.content)
            if not text or not text.strip():
                raise ProviderError("The OCI model returned an empty answer.")
            return Generation(text=text, model_id=s.oci_llm_model_id)

    def _compatible(self, system, prompt, config):
        s, identity = self.settings, self.identity
        if s.oci_llm_format == "responses":
            suffix = "/responses"
            body = dict(model=s.oci_llm_model_id, instructions=system, input=prompt,
                        max_output_tokens=config.max_tokens, store=False)
        else:
            suffix = "/chat/completions"
            body = dict(model=s.oci_llm_model_id, messages=[{"role": "system", "content": system},
                        {"role": "user", "content": prompt}], max_completion_tokens=config.max_tokens)
        signer = self.signer or oci.signer.Signer(identity["tenancy"], identity["user"], identity["fingerprint"],
                                  identity["key_file"], pass_phrase=identity.get("pass_phrase"))
        response = requests.post(s.oci_llm_endpoint.rstrip("/") + suffix, json=body, auth=signer,
            headers={"opc-compartment-id": s.oci_compartment_id}, timeout=(10, 120), allow_redirects=False)
        if response.status_code >= 300:
            raise ProviderError("OCI model invocation failed. Check model access and API format.")
        data = response.json()
        if s.oci_llm_format == "responses":
            if data.get("status") == "incomplete":
                raise ProviderError("The model response was incomplete.")
            return "".join(c.get("text", "") for item in data.get("output", []) for c in item.get("content", []) if c.get("type") == "output_text")
        if data["choices"][0].get("finish_reason") == "length":
            raise ProviderError("The model response was incomplete.")
        return data["choices"][0]["message"]["content"]
