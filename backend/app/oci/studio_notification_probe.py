"""No-send notification diagnostic. No credentials, addresses or payloads are returned."""
import hashlib
from copy import deepcopy
from threading import Lock
from time import monotonic


class NotificationProbe:
    def __init__(self, get_client, ttl=60):
        self.get_client = get_client
        self.ttl = ttl
        self.lock = Lock()
        self.cached = None
        self.checked_at = 0

    def check(self):
        # One bounded SDK inspection per process per minute, even on this public status route.
        if not self.lock.acquire(blocking=False):
            return {'version': 1, 'status': 'in_progress', 'publish': 'not_tested'}
        try:
            if self.cached is not None and monotonic() - self.checked_at < self.ttl:
                return deepcopy(self.cached)
            self.cached = self.inspect()
            self.checked_at = monotonic()
            return deepcopy(self.cached)
        finally:
            self.lock.release()

    def inspect(self):
        report = {'version': 1, 'status': 'unverified', 'topic_read': 'not_run',
                  'subscription_list': 'not_run', 'publish': 'not_tested'}
        control = data = None
        operation = 'credentials'
        try:
            import oci
            publisher = self.get_client()
            report.update(auth_mode=publisher.auth_mode, publishing_enabled=bool(publisher.enabled),
                          shared_poc_enabled=bool(publisher.shared_poc_enabled))
            if not publisher.topic_id:
                report['reason'] = 'topic_not_configured'
                return report
            report['topic_sha256'] = hashlib.sha256(publisher.topic_id.encode()).hexdigest()
            config, credentials = publisher._credentials()
            kwargs = dict(timeout=(5, 10), retry_strategy=oci.retry.NoneRetryStrategy(), **credentials)
            control = oci.ons.NotificationControlPlaneClient(config, **kwargs)
            operation = 'topic_read'
            topic = control.get_topic(publisher.topic_id).data
            report['topic_read'] = 'passed'
            if topic.lifecycle_state != 'ACTIVE':
                report['reason'] = 'topic_not_active'
                return report
            data = oci.ons.NotificationDataPlaneClient(config, service_endpoint=topic.api_endpoint, **kwargs)
            operation = 'subscription_list'
            page, active, pending = None, 0, 0
            deadline = monotonic() + 25
            for _ in range(10):
                if monotonic() >= deadline:
                    report['reason'] = 'inspection_time_limit'
                    return report
                result = data.list_subscriptions(topic.compartment_id, topic_id=publisher.topic_id,
                                                 limit=100, **({'page': page} if page else {}))
                for subscription in result.data:
                    if subscription.protocol != 'EMAIL' or subscription.lifecycle_state not in {'ACTIVE', 'PENDING'}:
                        report['reason'] = 'unexpected_subscription_type_or_state'
                        return report
                    active += subscription.lifecycle_state == 'ACTIVE'
                    pending += subscription.lifecycle_state == 'PENDING'
                page = result.headers.get('opc-next-page')
                if not page:
                    report.update(subscription_list='passed', active_email_subscriptions=active,
                                  pending_email_subscriptions=pending)
                    if not active:
                        report['reason'] = 'no_active_email_subscription'
                    else:
                        report['status'] = 'passed'
                    return report
            report['reason'] = 'subscription_page_limit'
        except Exception as exc:
            status = getattr(exc, 'status', None)
            rejected = status in {401, 403, 404}
            report[operation] = 'authorization_or_visibility_rejected' if rejected else 'unverified'
            report['status'] = 'authorization_or_visibility_rejected' if rejected else 'unverified'
            # Never return exception text, user identities, tokens, topic metadata or email addresses.
            if isinstance(status, int):
                report['http_status'] = status
        finally:
            for client in (data, control):
                if client is not None:
                    try:
                        client.base_client.session.close()
                    except Exception:
                        pass
        return report


def install_fastapi(app, get_client):
    from starlette.responses import JSONResponse
    probe = NotificationProbe(get_client)
    def check():
        report = probe.check()
        return JSONResponse(report, status_code=200 if report['status'] == 'passed' else 503,
                            headers={'Cache-Control': 'no-store'})
    app.add_api_route('/status/notifications', check, methods=['GET'], include_in_schema=False)


def install_mcp(mcp, get_client):
    import anyio
    from starlette.responses import JSONResponse
    probe = NotificationProbe(get_client)
    @mcp.custom_route('/status/notifications', methods=['GET'])
    async def check(request):
        report = await anyio.to_thread.run_sync(probe.check)
        return JSONResponse(report, status_code=200 if report['status'] == 'passed' else 503,
                            headers={'Cache-Control': 'no-store'})
