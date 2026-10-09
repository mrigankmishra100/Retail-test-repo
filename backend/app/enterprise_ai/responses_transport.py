"""Bounded OCI Responses REST transport with explicit IAM or GenAI-key auth.

IAM uses the same OCI signer as native Chat. Bearer auth is Responses-only and
never falls back to another identity. No retries or redirects:
a timed-out conversation write must not be replayed automatically.
"""
import json
import logging
from pathlib import Path
import re
from time import monotonic
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

from app.observability.context import request_trace_id

logger = logging.getLogger(__name__)
MAX_RESPONSE_BYTES = 2_000_000


def validate_genai_key(secret):
    value = secret.get_secret_value() if hasattr(secret, 'get_secret_value') else secret
    if (not isinstance(value, str) or not value.startswith('sk-') or not 4 <= len(value) <= 4096
            or not value.isascii() or any(ch.isspace() or ord(ch) < 33 or ord(ch) == 127 for ch in value)):
        raise ValueError('OCI_GENAI_API_KEY must be a populated GenAI secret without whitespace')
    return value


def normalize_endpoint(endpoint, project_id):
    parsed = urlsplit(endpoint or "")
    match = re.fullmatch(r"inference\.generativeai\.([a-z]+(?:-[a-z]+)+-\d+)\.oci\.oraclecloud\.com", parsed.netloc)
    if (parsed.scheme != "https" or not match or parsed.query or parsed.fragment
            or parsed.path.rstrip("/") not in {"", "/openai/v1"}):
        raise ValueError("Responses requires an OCI regional inference HTTPS endpoint")
    if not re.fullmatch(r"ocid1\.generativeaiproject\.oc1\." + re.escape(match[1]) + r"\.[A-Za-z0-9]+", project_id or ""):
        raise ValueError("Responses project must belong to the endpoint region")
    return f"https://{parsed.netloc}/openai/v1"


def conversation_path(identifier):
    if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}", identifier):
        raise ValueError("Invalid OCI conversation identifier")
    return "/conversations/" + identifier


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class OCIResponsesUnavailable(RuntimeError):
    """Safe public exception; provider payloads and credentials are not included."""


class OCIResponsesTransport:
    def __init__(self, *, endpoint, project_id, auth_mode="api_key",
                 config_file="~/.oci/config", config_profile="DEFAULT", timeout=60,
                 responses_auth_mode='oci', genai_api_key=None):
        self.base_url = normalize_endpoint(endpoint, project_id)
        if auth_mode not in {"api_key", "resource_principal", "instance_principal"}:
            raise ValueError("Unsupported OCI authentication mode")
        self.project_id, self.auth_mode = project_id, auth_mode
        self.config_file, self.config_profile = config_file, config_profile
        self.timeout = timeout
        if responses_auth_mode not in {'oci', 'genai_api_key'}:
            raise ValueError('Unsupported Responses authentication mode')
        self.responses_auth_mode = responses_auth_mode
        self._genai_api_key = genai_api_key
        if responses_auth_mode == 'genai_api_key':
            validate_genai_key(genai_api_key)

    def _signer(self):
        import oci
        if self.auth_mode == "resource_principal":
            return oci.auth.signers.get_resource_principals_signer()
        if self.auth_mode == "instance_principal":
            return oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
        config = oci.config.from_file(str(Path(self.config_file).expanduser()), self.config_profile)
        if config.get("security_token_file"):
            token = Path(config["security_token_file"]).expanduser().read_text().strip()
            private_key = oci.signer.load_private_key_from_file(
                str(Path(config["key_file"]).expanduser()), config.get("pass_phrase"))
            return oci.auth.signers.SecurityTokenSigner(token, private_key)
        oci.config.validate_config(config)
        return oci.signer.Signer(tenancy=config["tenancy"], user=config["user"],
            fingerprint=config["fingerprint"],
            private_key_file_location=str(Path(config["key_file"]).expanduser()),
            pass_phrase=config.get("pass_phrase"))

    def request(self, method, path, body=None):
        allowed = ((method == "POST" and path in {"/responses", "/conversations"})
                   or (method in {"GET", "DELETE"} and path.startswith("/conversations/")
                       and path == conversation_path(path.removeprefix("/conversations/")))
                   or (method == 'POST' and re.fullmatch(r'/vector_stores/[A-Za-z0-9_.-]{1,256}/search', path)))
        if not allowed:
            raise ValueError("Unsupported OCI Responses operation")
        operation = method + " " + path.split("/")[1]
        started, status, request_id, outcome = monotonic(), None, None, "unavailable"
        stage, error_type = 'request_preparation', None
        try:
            data = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8") if body is not None else None
            if data is not None and len(data) > MAX_RESPONSE_BYTES:
                raise ValueError("OCI request exceeds size limit")
            headers = {"Content-Type": "application/json", "OpenAI-Project": self.project_id}
            url = self.base_url + path
            if self.responses_auth_mode == 'genai_api_key':
                headers['Authorization'] = 'Bearer ' + validate_genai_key(self._genai_api_key)
            else:
                from oci._vendor import requests
                prepared = requests.Request(method, url, data=data, headers=headers).prepare()
                self._signer()(prepared)
                headers = dict(prepared.headers)
            request = Request(url, method=method, data=data, headers=headers)
            stage = 'provider_request'
            with build_opener(NoRedirect()).open(request, timeout=self.timeout) as response:
                status, request_id = response.status, response.headers.get("opc-request-id")
                stage = 'response_read'
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise ValueError("OCI response exceeds size limit")
                result = json.loads(raw) if raw else {}
                stage = 'response_validation'
                if not isinstance(result, dict):
                    raise ValueError("Invalid OCI response envelope")
                outcome = "success"
                return result
        except HTTPError as exc:
            error_type = type(exc).__name__
            status, request_id = exc.code, exc.headers.get("opc-request-id")
            exc.close()
            raise OCIResponsesUnavailable("OCI Responses service unavailable") from None
        except Exception as exc:
            error_type = type(exc).__name__
            raise OCIResponsesUnavailable("OCI Responses request failed") from None
        finally:
            safe_request_id = request_id if isinstance(request_id, str) and re.fullmatch(r"[A-Za-z0-9/_-]{1,256}", request_id) else None
            logger.info("OCI Responses operation=%s auth=%s status=%s http_status=%s opc_request_id=%s elapsed_ms=%d",
                operation, self.responses_auth_mode, outcome, status, safe_request_id, int((monotonic() - started) * 1000),
                extra={"trace_id": request_trace_id.get(), 'stage': stage, 'status': outcome,
                       'http_status': status, 'opc_request_id': safe_request_id,
                       'error_type': error_type, 'duration_ms': round((monotonic()-started)*1000, 2)})
