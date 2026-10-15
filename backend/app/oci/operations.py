"""Official OCI billing and centralized, metadata-only application logging."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from threading import Lock, Thread
from queue import Queue, Empty, Full
from time import monotonic
from uuid import uuid4
import json
import logging
import math
import re


def sdk_client(settings, client_type, *, region=None):
    import oci
    kwargs = {}
    if settings.oci_auth_mode == 'api_key':
        config = oci.config.from_file(str(Path(settings.oci_config_file).expanduser()), settings.oci_config_profile)
    else:
        signer = (oci.auth.signers.get_resource_principals_signer() if settings.oci_auth_mode == 'resource_principal'
                  else oci.auth.signers.InstancePrincipalsSecurityTokenSigner())
        config, kwargs = {}, {'signer': signer}
    config = dict(config)
    config['region'] = region or settings.oci_region or config.get('region')
    return client_type(config, timeout=(5, 15), retry_strategy=oci.retry.NoneRetryStrategy(), **kwargs)


def failure(exc):
    status = getattr(exc, 'status', None)
    return 'access_denied_or_not_found' if status in (401, 403, 404) else 'service_unavailable'


class OfficialCosts:
    """Compartment cost data from Usage API, never a token-rate estimate."""
    def __init__(self, settings):
        self.settings, self.lock, self.cached, self.expires = settings, Lock(), None, 0

    def read(self):
        s = self.settings
        if not s.oci_finops_enabled:
            return {'status': 'not_configured', 'source': 'OCI Usage API', 'totals': []}
        with self.lock:
            if self.cached is not None and monotonic() < self.expires:
                return self.cached
            try:
                self.cached = self._fetch()
            except Exception as exc:
                self.cached = {'status': failure(exc), 'source': 'OCI Usage API', 'totals': [],
                    'message': 'Official cost data unavailable. Verify tenancy, region and IAM access to read usage-report in tenancy.'}
            self.expires = monotonic() + 300
            return self.cached

    def _fetch(self):
        import oci
        s, m = self.settings, oci.usage_api.models
        tenant = s.oci_usage_tenancy_id
        if not tenant and s.oci_auth_mode == 'api_key':
            tenant = oci.config.from_file(str(Path(s.oci_config_file).expanduser()), s.oci_config_profile)['tenancy']
        if not tenant or not s.oci_compartment_id:
            return {'status': 'not_configured', 'source': 'OCI Usage API', 'totals': []}
        end = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        body = m.RequestSummarizedUsagesDetails(tenant_id=tenant, time_usage_started=end-timedelta(days=7),
            time_usage_ended=end, granularity='DAILY', query_type='COST', group_by=['service'],
            filter=m.Filter(operator='AND', dimensions=[m.Dimension(key='compartmentId', value=s.oci_compartment_id)]))
        client = sdk_client(s, oci.usage_api.UsageapiClient, region=s.oci_usage_region)
        page, rows = None, []
        for _ in range(20):
            response = client.request_summarized_usages(body, **({'page': page} if page else {}))
            for item in response.data.items:
                amount = item.computed_amount
                rows.append({'service': item.service, 'currency': item.currency,
                    'amount': str(amount) if amount is not None else None,
                    'date': item.time_usage_started.isoformat() if item.time_usage_started else None})
            page = response.headers.get('opc-next-page')
            if not page:
                break
        totals = {}
        complete = not page and all(r['amount'] is not None and r['currency'] for r in rows)
        if complete:
            for row in rows:
                amount = Decimal(row['amount'])
                if not amount.is_finite():
                    raise ValueError('Invalid cost amount')
                totals[row['currency']] = totals.get(row['currency'], Decimal(0)) + amount
        return {'status': 'available' if rows and complete else 'partial' if rows else 'no_data',
            'source': 'OCI Usage API', 'scope': 'All services in the configured compartment; not per-model or per-session billing',
            'from': (end-timedelta(days=7)).isoformat(), 'to': end.isoformat(),
            'fetched_at': datetime.now(timezone.utc).isoformat(),
            'totals': [{'currency': c, 'amount': str(v)} for c, v in totals.items()],
            'rows': rows, 'message': 'Provider-reported costs can arrive late and may be adjusted. No estimated costs are included.'}


# Only operational scalar fields. No message text, prompts, tool arguments,
# result payloads, SQL, exception text, session credentials or supplier identities.
SAFE_FIELDS = ('trace_id', 'model_id', 'stage', 'status', 'mcp_tool', 'graph_node', 'agent',
               'duration_ms', 'http_status', 'error_type', 'service', 'method', 'request_id', 'api_source', 'event', 'route', 'error_code')


def safe_fields(data):
    return {key: value for key, value in data.items()
            if key in SAFE_FIELDS + ('timestamp', 'level', 'logger') and
            (isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
             or isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.:/ {}+\-]{1,256}', value))}


def metadata(record):
    result = {'timestamp': datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
              'level': record.levelname, 'logger': record.name}
    result.update({key: getattr(record, key, None) for key in SAFE_FIELDS})
    # Only fixed event names, never formatted messages or request payloads.
    if record.msg in ('request_completed', 'chat_execution_event', 'retail_chat_tool_completed',
                      'mcp_control_check', 'mcp_tool_failed', 'mcp_boundary_failed', 'mcp_boundary_completed',
                      'mcp_remote_started', 'mcp_remote_completed', 'mcp_remote_failed', 'mcp_remote_stage'):
        result['event'] = record.msg
    return safe_fields(result)


class CentralLogs(logging.Handler):
    def __init__(self, settings):
        super().__init__(logging.INFO)
        self.settings, self.queue, self.worker = settings, Queue(maxsize=2000), None
        self.accepted, self.dropped, self.last_error = 0, 0, None
        self.stopping, self.lock_start = False, Lock()

    def start(self):
        if not self.settings.oci_application_log_id:
            return
        with self.lock_start:
            if self.worker is None:
                self.worker = Thread(target=self._run, name='oci-application-logs', daemon=True)
                self.worker.start()
                logging.getLogger().addHandler(self)

    def emit(self, record):
        if self.stopping or not record.name.startswith('app.'):
            return
        row = metadata(record)
        from app.chat_telemetry import active_trace
        trace = active_trace.get()
        if trace:
            row.setdefault('trace_id', trace.id)
            row.setdefault('model_id', trace.profile.model_id)
        # Infrastructure library messages without useful structured context are skipped.
        if not any(k in row for k in ('stage', 'graph_node', 'status', 'mcp_tool')):
            return
        try:
            self.queue.put_nowait(row)
        except Full:
            self.dropped += 1

    def _run(self):
        while not self.stopping or not self.queue.empty():
            try:
                first = self.queue.get(timeout=0.25)
            except Empty:
                continue
            batch = [first]
            while len(batch) < 50:
                try:
                    batch.append(self.queue.get_nowait())
                except Empty:
                    break
            try:
                self.send(batch)
                self.accepted += len(batch)
                self.last_error = None
            except Exception as exc:
                self.last_error = failure(exc)
                self.dropped += len(batch)
            finally:
                for _ in batch:
                    self.queue.task_done()

    def send(self, batch):
        import oci
        m = oci.loggingingestion.models
        client = sdk_client(self.settings, oci.loggingingestion.LoggingClient)
        entries = [m.LogEntry(id=str(uuid4()), data=json.dumps(row),
            time=datetime.fromisoformat(row['timestamp'])) for row in batch]
        client.put_logs(self.settings.oci_application_log_id, m.PutLogsDetails(specversion='1.0',
            log_entry_batches=[m.LogEntryBatch(entries=entries, source='retail-inventory-agent',
                type='application.execution', subject=self.settings.application_role,
                defaultlogentrytime=datetime.now(timezone.utc))]))

    def status(self):
        return {'status': self.last_error or ('connected' if self.accepted else 'configured' if self.settings.oci_application_log_id else 'not_configured'),
                'accepted_events': self.accepted, 'dropped_events': self.dropped, 'queued_events': self.queue.qsize()}

    def search(self, hours=1):
        import oci
        if hours not in (1, 6, 24):
            raise ValueError('Unsupported log window')
        s = self.settings
        now = datetime.now(timezone.utc)
        context = {'source': 'OCI Logging Search API', 'region': s.oci_region,
                   'from': (now-timedelta(hours=hours)).isoformat(), 'to': now.isoformat(),
                   'fetched_at': now.isoformat(), 'limit': 50,
                   'scope': 'Configured application log, across app sessions'}
        if not s.oci_application_log_id:
            return {**self.status(), **context, 'search_status': 'not_configured', 'events': [],
                    'message': 'Configure an OCI application log and log group to read cloud events.'}
        try:
            scope = f'{s.oci_compartment_id}/{s.oci_application_log_group_id}/{s.oci_application_log_id}'
            client = sdk_client(s, oci.loggingsearch.LogSearchClient)
            response = client.search_logs(oci.loggingsearch.models.SearchLogsDetails(
                time_start=now-timedelta(hours=hours), time_end=now,
                search_query=f'search "{scope}" | sort by datetime desc', is_return_field_info=False), limit=50)
            events = []
            for result in response.data.results or []:
                content = result.data.get('logContent', {})
                data = content.get('data', {})
                if isinstance(data, str):
                    try:
                        data = json.loads(data)
                    except ValueError:
                        continue
                if isinstance(data, dict):
                    row = safe_fields(data)
                    if row:
                        events.append(row)
            return {**self.status(), **context, 'search_status': 'available', 'events': events,
                'message': f'Latest 50 events in the last {hours} hour(s), across app sessions. OCI ingestion may be delayed.'}
        except Exception as exc:
            return {**self.status(), **context, 'search_status': failure(exc), 'events': [],
                    'message': 'Could not read OCI logs. Verify the log configuration, region and Logging Search IAM permissions.'}

    def close(self):
        self.stopping = True
        logging.getLogger().removeHandler(self)
        if self.worker:
            self.worker.join(timeout=3)
        super().close()
