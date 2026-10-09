"""Bounded private JSON configuration, loaded once per settings construction.

Explicit constructor arguments > JSON > individual environment variables > defaults.
JSON wins over Docker ENV defaults. Bootstrap coordinates always come from the
environment, never from the downloaded object. No credential values are logged.
"""
import json
import os
import logging
from time import monotonic
from app.utils.logger import ensure_logging, provider_metadata

logger = logging.getLogger(__name__)

MAX_CONFIG_BYTES = 64 * 1024


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate configuration key')
        result[key] = value
    return result


def load_json_settings(settings_class):
    inline = os.getenv('APP_CONFIG_JSON')
    coordinates = [os.getenv(name) for name in
                   ('APP_CONFIG_NAMESPACE', 'APP_CONFIG_BUCKET', 'APP_CONFIG_OBJECT_NAME')]
    if inline is None and not any(coordinates):
        return {}
    ensure_logging()
    started = monotonic()
    stage, reason = 'source_selection', 'source_conflict'
    source = 'inline' if inline is not None else 'object_storage'
    try:
        if inline is not None and any(coordinates):
            raise ValueError('Choose only one JSON configuration source')
        if inline is not None:
            raw = inline.encode('utf-8')
        else:
            reason = 'incomplete_coordinates'
            if not all(coordinates):
                raise ValueError('Incomplete configuration object coordinates')
            from app.oci.storage import create_object_storage_client
            stage, reason = 'client_initialization', 'client_unavailable'
            client = create_object_storage_client(
                config_file=os.getenv('OCI_CONFIG_FILE', '~/.oci/config'),
                config_profile=os.getenv('OCI_CONFIG_PROFILE', 'DEFAULT'),
                region=os.getenv('OCI_REGION'),
                auth_mode=os.getenv('OCI_AUTH_MODE', 'resource_principal'))
            try:
                stage, reason = 'object_download', 'object_unavailable'
                response = client.get_object(*coordinates)
                try:
                    stage, reason = 'object_read', 'read_failed'
                    raw = response.data.raw.read(MAX_CONFIG_BYTES + 1)
                finally:
                    response.data.close()
            finally:
                client.base_client.session.close()
        stage, reason = 'size_check', 'size_limit'
        if len(raw) > MAX_CONFIG_BYTES:
            raise ValueError('Configuration exceeds size limit')
        stage, reason = 'json_parse', 'invalid_json_or_duplicate_key'
        data = json.loads(raw, object_pairs_hook=_unique)
        stage, reason = 'key_validation', 'invalid_keys_or_shape'
        if not isinstance(data, dict):
            raise ValueError('Configuration must be an object')
        if len({key.lower() for key in data}) != len(data):
            raise ValueError('Duplicate configuration setting')
        data = {key.lower(): value for key, value in data.items()}
        allowed = set(settings_class.model_fields)
        # Retain the documented alternate notification variable name.
        if 'oci_notification_ocid' in data:
            if 'oci_notification_topic_id' in data:
                raise ValueError('Duplicate notification setting')
            data['oci_notification_topic_id'] = data.pop('oci_notification_ocid')
        if set(data) - allowed:
            raise ValueError('Unknown configuration field')
        result = {}
        for name, value in data.items():
            field = settings_class.model_fields[name]
            alias = field.validation_alias
            # Pydantic fields with AliasChoices are populated via their first alias.
            target = alias.choices[0] if hasattr(alias, 'choices') else alias or name
            result[target] = value
        logger.info('config_loaded', extra={'source':source,'stage':'completed',
            'setting_count':len(result),'duration_ms':round((monotonic()-started)*1000,2)})
        return result
    except Exception as exc:
        metadata = provider_metadata(exc)
        if stage == 'object_download':
            reason = {401:'authentication_failed',403:'access_denied',404:'object_not_found_or_not_authorized'}.get(
                metadata['http_status'], reason)
        logger.error('config_load_failed', extra={'source':source,'stage':stage,'reason':reason,
            'error_type':type(exc).__name__, 'duration_ms':round((monotonic()-started)*1000,2), **metadata})
        # Do not include SDK errors, object contents, parser input or credentials.
        raise ValueError('Private JSON configuration is invalid or unavailable') from None


class JsonSettingsMixin:
    def __init__(self, **values):
        try:
            super().__init__(**values)
        except Exception as exc:
            from pydantic import ValidationError
            if isinstance(exc, ValidationError):
                ensure_logging()
                logger.error('settings_validation_failed', extra={
                    'stage':'settings_validation', 'reason':'invalid_setting_values',
                    'error_type':'ValidationError'})
            raise

    @classmethod
    def settings_customise_sources(cls, settings_cls, init_settings, env_settings,
                                   dotenv_settings, file_secret_settings):
        return (init_settings, lambda: load_json_settings(settings_cls),
                env_settings, dotenv_settings, file_secret_settings)
