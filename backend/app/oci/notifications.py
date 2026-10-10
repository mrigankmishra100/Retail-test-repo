"""Opt-in OCI publisher used only by the approval-verifying outbox service."""

from pathlib import Path
from threading import Lock
import json
import logging
from time import monotonic
from uuid import UUID
from app.utils.logger import provider_metadata

logger = logging.getLogger(__name__)
SAFE_CODES = frozenset({'notification_publishing_disabled', 'notification_setup_failed',
    'notification_destination_changed', 'missing_publish_acknowledgement',
    'publish_rejected', 'publish_uncertain', 'supplier_destination_invalid',
    'shared_poc_subscription_invalid', 'shared_poc_no_active_email',
    'shared_poc_subscription_limit', 'supplier_subscription_invalid', 'supplier_subscription_check_failed'})


class NotificationDeliveryError(RuntimeError):
    def __init__(self, code, *, not_delivered=False, retryable=False, provider_info=None):
        self.code, self.not_delivered, self.retryable = code, not_delivered, retryable
        self.provider_info = provider_info or {}
        super().__init__(code)


class OCINotificationClient:
    def __init__(self, topic_id=None, *, enabled=False, config_file="~/.oci/config", config_profile="DEFAULT", supplier_topics=None,
                 shared_poc_enabled=False, auth_mode='api_key'):
        self.topic_id, self.enabled = topic_id, enabled
        self.config_file, self.config_profile = config_file, config_profile
        self._client = None
        self._lock = Lock()
        self.supplier_topics = dict(supplier_topics or {})
        self.shared_poc_enabled = shared_poc_enabled
        if auth_mode not in {'api_key', 'resource_principal', 'instance_principal'}:
            raise ValueError('Unsupported notification authentication mode')
        self.auth_mode = auth_mode

    def _credentials(self):
        import oci
        credentials = {}
        if self.auth_mode == 'resource_principal':
            config = {}
            credentials['signer'] = oci.auth.signers.get_resource_principals_signer()
        elif self.auth_mode == 'instance_principal':
            config = {}
            credentials['signer'] = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
        else:
            config = oci.config.from_file(str(Path(self.config_file).expanduser()), self.config_profile)
        parts = (self.topic_id or '').split('.')
        if len(parts) < 5 or parts[1] != 'onstopic' or not parts[3]:
            raise ValueError('Invalid notification topic')
        config['region'] = parts[3]
        return config, credentials

    def ensure_ready(self):
        if not self.enabled or not (self.topic_id or self.supplier_topics):
            raise NotificationDeliveryError("notification_publishing_disabled", not_delivered=True)

    def _get_client(self):
        with self._lock:
            if self._client is None:
                import oci
                try:
                    config, credentials = self._credentials()
                    control = oci.ons.NotificationControlPlaneClient(config, timeout=(10, 20),
                                                                     retry_strategy=oci.retry.NoneRetryStrategy(), **credentials)
                    try:
                        endpoint = control.get_topic(self.topic_id).data.api_endpoint
                    finally:
                        control.base_client.session.close()
                    self._client = oci.ons.NotificationDataPlaneClient(config, service_endpoint=endpoint,
                        timeout=(10, 20), retry_strategy=oci.retry.NoneRetryStrategy(), **credentials)
                except Exception as exc:
                    raise NotificationDeliveryError("notification_setup_failed", not_delivered=True,
                        provider_info=provider_metadata(exc)) from None
            return self._client

    def publish(self, destination, message, notification_id):
        started = monotonic()
        try:
            safe_id = str(UUID(str(notification_id)))
        except (ValueError, TypeError, AttributeError):
            safe_id = None
        metadata = {'notification_id':safe_id, 'auth_mode':self.auth_mode}
        logger.info('notification_publish_started', extra=metadata)
        try:
            result = self._publish(destination, message, notification_id)
            logger.info('notification_publish_acknowledged', extra={**metadata,
                'status':'acknowledged', 'duration_ms':round((monotonic()-started)*1000,2)})
            return result
        except NotificationDeliveryError as exc:
            reason = exc.code if exc.code in SAFE_CODES else 'notification_failure'
            outcome = ('rejected' if reason == 'publish_rejected' else 'preflight_failed') if exc.not_delivered else 'uncertain'
            logger.warning('notification_publish_failed', extra={**metadata,'status':outcome,
                'reason':reason, 'not_delivered':exc.not_delivered, 'retryable':exc.retryable,
                'http_status':exc.provider_info.get('http_status'),
                'opc_request_id':exc.provider_info.get('opc_request_id'),
                'duration_ms':round((monotonic()-started)*1000,2)})
            raise
        except Exception as exc:
            logger.error('notification_publish_failed', extra={**metadata,'status':'failed',
                'reason':'unexpected_error', 'error_type':type(exc).__name__,
                'duration_ms':round((monotonic()-started)*1000,2)})
            raise

    def _publish(self, destination, message, notification_id):
        self.ensure_ready()
        payload = json.loads(message)
        if payload.get("email") is not None or payload.get("response_email") is not None:
            return self._publish_supplier_email(destination, payload, notification_id)
        if destination != self.topic_id:
            raise NotificationDeliveryError("notification_destination_changed", not_delivered=True)
        client = self._get_client()
        import oci
        try:
            response = client.publish_message(destination,
                oci.ons.models.MessageDetails(body=message, title="Approved POC case update"),
                message_type="RAW_TEXT", opc_request_id=notification_id, retry_strategy=oci.retry.NoneRetryStrategy())
            message_id = response.data.message_id
            if not message_id:
                raise NotificationDeliveryError("missing_publish_acknowledgement")
            logger.info('notification_provider_acknowledged', extra=provider_metadata(response))
            return message_id
        except NotificationDeliveryError:
            raise
        except Exception as exc:
            status = getattr(exc, "status", None)
            raise NotificationDeliveryError("publish_rejected" if status in {400, 401, 403, 404, 429} else "publish_uncertain",
                not_delivered=status in {400, 401, 403, 404, 429}, retryable=status == 429,
                provider_info=provider_metadata(exc)) from None

    def _publish_supplier_email(self, destination, payload, notification_id):
        """Dispatch the approved isolated or explicitly reviewed shared-POC snapshot."""
        from app.contracts import SupplierEmailDraft, SupplierResponseDraft
        response = payload.get("response_email") is not None
        if response:
            if payload.get("email") is not None:
                raise NotificationDeliveryError("supplier_destination_invalid", not_delivered=True)
            email = SupplierResponseDraft.model_validate(payload["response_email"])
        else:
            email = SupplierEmailDraft.model_validate(payload["email"])
        shared = email.delivery_mode == "shared_poc_topic"
        if payload.get("kind") != ("SUPPLIER_RESPONSE" if response else "SUPPLIER_REQUEST") or (response and not shared):
            raise NotificationDeliveryError("supplier_destination_invalid", not_delivered=True)
        if shared:
            if not self.shared_poc_enabled or not destination or destination != self.topic_id:
                raise NotificationDeliveryError("supplier_destination_invalid", not_delivered=True)
        elif (destination == self.topic_id or self.supplier_topics.get(payload.get("supplier_id")) != destination
                or list(self.supplier_topics.values()).count(destination) != 1):
            raise NotificationDeliveryError("supplier_destination_invalid", not_delivered=True)
        child = OCINotificationClient(destination, enabled=True,
            config_file=self.config_file, config_profile=self.config_profile, auth_mode=self.auth_mode)
        try:
            client = child._get_client()
            child._verify_email_subscription(client, None if response else email.recipient, shared_poc=shared)
            import oci
            try:
                result = client.publish_message(destination,
                    oci.ons.models.MessageDetails(title=email.subject, body=email.body),
                    message_type="RAW_TEXT", opc_request_id=notification_id,
                    retry_strategy=oci.retry.NoneRetryStrategy())
                message_id = result.data.message_id
                if not message_id:
                    raise NotificationDeliveryError("missing_publish_acknowledgement")
                logger.info('notification_provider_acknowledged', extra=provider_metadata(result))
                return message_id
            except NotificationDeliveryError:
                raise
            except Exception as exc:
                status = getattr(exc, "status", None)
                raise NotificationDeliveryError("publish_rejected" if status in {400, 401, 403, 404, 429} else "publish_uncertain",
                    not_delivered=status in {400, 401, 403, 404, 429}, retryable=status == 429,
                    provider_info=provider_metadata(exc)) from None
        finally:
            child.close()

    def _verify_email_subscription(self, client, recipient, *, shared_poc=False):
        """Check isolated recipient or explicitly opted-in POC email audience.

        IAM must prohibit subscription edits during dispatch: metadata check and publish
        cannot be atomic across OCI APIs. No automatic subscription creation/confirmation.
        """
        import oci
        try:
            config, credentials = self._credentials()
            control = oci.ons.NotificationControlPlaneClient(config, timeout=(10, 20),
                retry_strategy=oci.retry.NoneRetryStrategy(), **credentials)
            try:
                topic = control.get_topic(self.topic_id).data
            finally:
                control.base_client.session.close()
            if shared_poc:
                active, pending, page = 0, 0, None
                for _ in range(10):
                    options = {"page": page} if page else {}
                    response = client.list_subscriptions(topic.compartment_id, topic_id=self.topic_id, limit=100, **options)
                    for subscription in response.data:
                        if subscription.protocol != "EMAIL" or subscription.lifecycle_state not in {"ACTIVE", "PENDING"}:
                            raise NotificationDeliveryError("shared_poc_subscription_invalid", not_delivered=True)
                        active += subscription.lifecycle_state == "ACTIVE"
                        pending += subscription.lifecycle_state == "PENDING"
                    page = response.headers.get("opc-next-page")
                    if not page:
                        if not active:
                            raise NotificationDeliveryError("shared_poc_no_active_email", not_delivered=True)
                        return {"active": active, "pending": pending}
                raise NotificationDeliveryError("shared_poc_subscription_limit", not_delivered=True)
            # Isolated mode requires exactly one matching subscriber.
            response = client.list_subscriptions(topic.compartment_id, topic_id=self.topic_id, limit=2)
            subscriptions = response.data
            if (response.headers.get("opc-next-page") or len(subscriptions) != 1
                    or subscriptions[0].protocol != "EMAIL" or subscriptions[0].lifecycle_state != "ACTIVE"
                    or subscriptions[0].endpoint != recipient):
                raise NotificationDeliveryError("supplier_subscription_invalid", not_delivered=True)
        except NotificationDeliveryError:
            raise
        except Exception as exc:
            raise NotificationDeliveryError("supplier_subscription_check_failed", not_delivered=True,
                provider_info=provider_metadata(exc)) from None

    async def publish_approved(self, message, approval_context):
        raise PermissionError("Use the database-backed dispatcher; caller-supplied approval is not trusted")

    def close(self):
        with self._lock:
            if self._client is not None:
                self._client.base_client.session.close()
                self._client = None
