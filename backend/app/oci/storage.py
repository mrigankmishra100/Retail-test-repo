"""OCI Object Storage client boundary for supplier policy PDFs."""

from pathlib import Path
import re
from threading import Lock


MAX_POLICY_BYTES = 2_000_000


def create_object_storage_client(*, config_file, config_profile, region, auth_mode):
    """Construct an SDK client lazily, without sharing wallet/policy buckets."""
    import oci
    credentials = {}
    if auth_mode == 'resource_principal':
        config = {}
        credentials['signer'] = oci.auth.signers.get_resource_principals_signer()
    elif auth_mode == 'instance_principal':
        config = {}
        credentials['signer'] = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
    elif auth_mode == 'api_key':
        config = oci.config.from_file(str(Path(config_file).expanduser()), config_profile)
    else:
        raise ValueError('Unsupported OCI authentication mode')
    if region:
        config['region'] = region
    return oci.object_storage.ObjectStorageClient(
        config, timeout=(10, 20), retry_strategy=oci.retry.NoneRetryStrategy(), **credentials)


class PolicyStorageError(RuntimeError):
    """A safe failure code for fallback handling; raw SDK details stay private."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class OCIStorageClient:
    """Read policies lazily; explicit uploads never overwrite existing objects."""

    def __init__(self, namespace: str | None = None, bucket_name: str | None = None, *,
                 config_file: str = "~/.oci/config", config_profile: str = "DEFAULT",
                 region: str | None = None, auth_mode: str = 'api_key') -> None:
        if auth_mode not in {'api_key', 'resource_principal', 'instance_principal'}:
            raise ValueError('Unsupported OCI authentication mode')
        self.auth_mode = auth_mode
        self.namespace = namespace
        self.bucket_name = bucket_name
        self.config_file, self.config_profile, self.region = config_file, config_profile, region
        self._client = None
        self._lock = Lock()

    @staticmethod
    def validate_name(object_name: str) -> None:
        if not re.fullmatch(r"SUP[0-9]{3}_policy\.pdf", object_name):
            raise PolicyStorageError("invalid_object_name")

    def _get_client(self):
        if not self.namespace or not self.bucket_name:
            raise PolicyStorageError("storage_not_configured")
        with self._lock:
            if self._client is None:
                self._client = create_object_storage_client(config_file=self.config_file,
                    config_profile=self.config_profile, region=self.region, auth_mode=self.auth_mode)
            return self._client

    @staticmethod
    def _failure(exc: Exception) -> PolicyStorageError:
        status = getattr(exc, "status", None)
        return PolicyStorageError({401: "authentication_failed", 403: "access_denied",
                                   404: "object_not_found"}.get(status, "storage_unavailable"))

    def get_policy_document(self, object_name: str) -> bytes:
        """Download at most 2 MB and always close the HTTP response."""
        self.validate_name(object_name)
        try:
            response = self._get_client().get_object(self.namespace, self.bucket_name, object_name)
            try:
                document = response.data.raw.read(MAX_POLICY_BYTES + 1)
            finally:
                response.data.close()
            if len(document) > MAX_POLICY_BYTES:
                raise PolicyStorageError("policy_too_large")
            return document
        except PolicyStorageError:
            raise
        except Exception as exc:
            raise self._failure(exc) from exc

    def upload_policy_document(self, path: Path, object_name: str) -> bool:
        """Create a validated policy object, failing if it already exists."""
        self.validate_name(object_name)
        from app.services.policy import extract_policy_document
        try:
            with path.open("rb") as stream:
                document = stream.read(MAX_POLICY_BYTES + 1)
            extract_policy_document(document, object_name[:6], object_name)
            self._get_client().put_object(
                self.namespace, self.bucket_name, object_name, document,
                content_type="application/pdf", content_length=len(document), if_none_match="*")
        except Exception as exc:
            raise PolicyStorageError("upload_failed_or_object_exists") from exc
        return True

    def close(self) -> None:
        with self._lock:
            if self._client is not None:
                self._client.base_client.session.close()
                self._client = None
