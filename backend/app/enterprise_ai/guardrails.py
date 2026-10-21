"""OCI detection plus application enforcement. Never log evaluated content."""
import asyncio
from functools import cached_property
import logging
import math
from pathlib import Path
import re
from time import monotonic
from urllib.parse import urlsplit
from uuid import uuid4
from app.utils.logger import provider_metadata

logger = logging.getLogger(__name__)
BLOCK_MESSAGE = "This request or response was blocked by the application's security policy."
FAILURE_MESSAGE = "Security checks are unavailable. Please retry later; no further AI processing was performed."
SECRET_PATTERN = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
    r"|\b(?:api[_ -]?key|access[_ -]?token|password|client[_ -]?secret)\s*[:=]\s*[\"']?[^\s\"']{8,}", re.IGNORECASE)

class GuardrailDenied(RuntimeError):
    def __init__(self, verdict):
        super().__init__("Security policy denied processing")
        self.verdict = verdict


def public_security_decision(verdict):
    """Expose only fixed decision categories and bounded SDK metadata, never raw bodies."""
    from app.contracts import SecurityDecision
    categories, status = verdict.get('categories', []), verdict['status']
    reason = ('security_check_unavailable' if status == 'FAILED' else
              'prompt_injection_detected' if 'prompt_injection' in categories else
              'credential_material_detected' if 'credential_material' in categories else
              'action_requires_approval' if 'action_requires_approval' in categories else
              'unverified_action_claim' if 'unverified_action_claim' in categories else 'checks_passed')
    return SecurityDecision(status=status,
        provider={'oci': 'oci', 'custom': 'local'}.get(verdict.get('provider'), 'unknown'),
        reason_code=reason, stage=verdict.get('stage', 'input'), source=verdict.get('source', 'user'),
        api_called=verdict.get('api_called', False), event_id=verdict.get('event_id'),
        prompt_injection_score=verdict.get('prompt_injection_score'), version=verdict.get('version'),
        duration_ms=verdict.get('duration_ms'), http_status=verdict.get('http_status'),
        request_id=verdict.get('request_id')).model_dump(mode='json')


def local_security_decision(category, *, source='user'):
    details = public_security_decision({'status': 'BLOCKED', 'provider': 'custom',
        'stage': 'action', 'source': source, 'categories': [category], 'event_id': str(uuid4())})
    from app.chat_telemetry import active_trace
    trace = active_trace.get()
    if trace is not None:
        trace.security_checks.append(details)
    return details

def normalize_guardrails_endpoint(endpoint):
    parsed = urlsplit(endpoint or "")
    if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.port
            or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
            or not re.fullmatch(r"inference\.generativeai\.[a-z0-9-]+\.oci\.oraclecloud\.com", parsed.hostname or "")):
        raise ValueError("Guardrails requires a commercial OCI inference HTTPS origin without an API path")
    return f"https://{parsed.hostname}"

class EnterpriseAIGuardrailsClient:
    """Lazy signed ApplyGuardrails calls; incomplete results deny processing."""
    def __init__(self, endpoint=None, enabled=False, *, compartment_id=None,
                 config_file="~/.oci/config", config_profile="DEFAULT", auth_mode="api_key",
                 version="1.0.1", timeout=15,
                 custom_rules_enabled=True, max_chars=24000):
        self.endpoint, self.enabled, self.compartment_id = endpoint, enabled, compartment_id
        self.config_file, self.config_profile, self.auth_mode = config_file, config_profile, auth_mode
        self.version, self.timeout = version, timeout
        self.custom_rules_enabled, self.max_chars = custom_rules_enabled, max_chars

    @classmethod
    def from_settings(cls, settings):
        return cls(endpoint=settings.enterprise_ai_guardrails_endpoint,
            enabled=settings.prompt_guardrails_enabled, compartment_id=settings.oci_compartment_id,
            config_file=settings.oci_config_file, config_profile=settings.oci_config_profile,
            auth_mode=settings.oci_auth_mode, version=settings.enterprise_ai_guardrails_version,
            timeout=settings.enterprise_ai_guardrails_timeout_seconds, custom_rules_enabled=False)

    @cached_property
    def client(self):
        import oci
        endpoint = normalize_guardrails_endpoint(self.endpoint)
        if not self.compartment_id:
            raise ValueError("Guardrails compartment is required")
        auth = {}
        if self.auth_mode == "resource_principal":
            config = {}
            auth["signer"] = oci.auth.signers.get_resource_principals_signer()
        elif self.auth_mode == "instance_principal":
            config = {}
            auth["signer"] = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
        elif self.auth_mode == "api_key":
            config = oci.config.from_file(str(Path(self.config_file).expanduser()), self.config_profile)
        else:
            raise ValueError("Unsupported OCI authentication mode")
        return oci.generative_ai_inference.GenerativeAiInferenceClient(
            config, service_endpoint=endpoint, retry_strategy=oci.retry.NoneRetryStrategy(),
            timeout=(min(5, self.timeout), self.timeout), **auth)

    async def evaluate(self, content, role, **context):
        return await asyncio.to_thread(self.evaluate_sync, content, role, **context)

    def evaluate_sync(self, content, role, *, stage="input", source="user"):
        if not self.enabled:
            return {"status": "NOT_APPLIED", "allowed": None, "provider": "disabled", "categories": []}
        started, event_id = monotonic(), str(uuid4())
        verdict = {"status": "FAILED", "allowed": False, "provider": "oci",
                   "categories": [], "event_id": event_id, "stage": stage, "source": source, 'api_called': False}
        metadata = {}
        try:
            if role not in {"retail-manager", "supplier", "distributor"}:
                raise ValueError("Unsupported security role")
            if not isinstance(content, str) or not content.strip() or len(content) > self.max_chars:
                raise ValueError("Security content outside application size bounds")
            if self.custom_rules_enabled and SECRET_PATTERN.search(content):
                verdict.update(status="BLOCKED", provider="custom", categories=["credential_material"])
            else:
                import oci
                models = oci.generative_ai_inference.models
                details = models.ApplyGuardrailsDetails(
                    compartment_id=self.compartment_id,
                    input=models.GuardrailsTextInput(content=content, language_code="en"),
                    guardrail_configs=models.GuardrailConfigs(
                        prompt_injection_config=models.PromptInjectionConfiguration()),
                    guardrail_version_config=models.GuardrailVersionConfig(guardrail_version=self.version))
                client = self.client
                verdict['api_called'] = True
                response = client.apply_guardrails(details, opc_request_id=event_id)
                metadata = provider_metadata(response)
                verdict.update(self._decision(oci.util.to_dict(response.data)))
        except Exception as exc:
            metadata.update({key: value for key, value in provider_metadata(exc).items() if value is not None})
            metadata['error_type'] = type(exc).__name__
            verdict.update(status="FAILED", allowed=False, reason="guardrails_unavailable_or_invalid")
        verdict["duration_ms"] = round((monotonic() - started) * 1000, 2)
        verdict.update(http_status=metadata.get('http_status'), request_id=metadata.get('opc_request_id'))
        logger.info("security_guardrail_decision", extra={
            "role": role, "stage": stage, "source": source, "status": verdict["status"],
            "security_event_id": event_id, "security_provider": verdict["provider"],
            "security_categories": verdict["categories"], "guardrail_version": verdict.get("version"),
            "duration_ms": verdict["duration_ms"], **metadata})
        from app.chat_telemetry import event, active_trace
        from app.live_activity import activity_note
        event('security', verdict['status'].lower(), tool='prompt_injection:' + stage,
              duration_ms=verdict['duration_ms'])
        activity_note('security', 'Prompt injection check · ' + stage, verdict['status'].lower())
        trace = active_trace.get()
        if trace is not None and len(trace.security_checks) < 40:
            trace.security_checks.append(public_security_decision(verdict))
        return verdict

    def require_sync(self, content, role, **context):
        verdict = self.evaluate_sync(content, role, **context)
        if self.enabled and verdict["allowed"] is not True:
            raise GuardrailDenied(verdict)
        return verdict

    def _decision(self, data):
        results = data["results"]
        pi_score = self._score(results["prompt_injection"]["score"])
        version = data["guardrail_version"]["version"]
        if version != self.version:
            raise ValueError("Unexpected guardrails version")
        violations = []
        if pi_score > 0:
            violations.append("prompt_injection")
        return {"status": "BLOCKED" if violations else "PASSED", "allowed": not violations,
                "categories": violations, "version": version, 'prompt_injection_score': pi_score}

    @staticmethod
    def _score(score):
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score) or not 0 <= score <= 1:
            raise ValueError("Invalid detection score")
        return score
