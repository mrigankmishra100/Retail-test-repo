"""Credential-safe database diagnostics. Never serialize exceptions or SDK objects."""
from contextlib import contextmanager
import logging
import re
from time import perf_counter

logger = logging.getLogger(__name__)
# Driver messages can embed the listener reason as "(Similar to ORA-12506)".
# Match only complete numeric identifiers, never surrounding message text or a
# code-like substring inside a longer identifier.
_ORACLE_CODE = re.compile(r"(?<![\w-])((?:ORA|DPY|DPI)-[0-9]{4,5})(?![\w-])")
_WALLET_CODES = frozenset({
    'wallet_size_invalid', 'wallet_archive_limits_exceeded',
    'wallet_archive_path_invalid', 'encrypted_zip_not_supported',
    'wallet_archive_ambiguous_or_oversized', 'wallet_file_size_invalid',
    'wallet_required_files_missing', 'wallet_pem_invalid', 'wallet_archive_invalid',
    'wallet_authentication_failed', 'wallet_access_denied', 'wallet_object_not_found',
    'wallet_download_failed', 'wallet_materialization_failed',
})
_ERROR_TYPES = frozenset({
    'DatabaseError', 'OperationalError', 'InterfaceError', 'ProgrammingError',
    'IntegrityError', 'DataError', 'NotSupportedError', 'InternalError',
    'OracleDatabaseConfigurationError', 'WalletLoadError', 'TimeoutError',
    'ConnectionError', 'PermissionError', 'FileNotFoundError', 'OSError',
    'ValueError', 'TypeError', 'RuntimeError', 'SSLError',
})


def failure_fields(exc):
    """Extract only fixed wallet codes and numeric Oracle codes, including causes.

    Messages are inspected in memory, never emitted. Bounded traversal handles
    driver wrappers and cycles; no traceback, filenames, arguments or SQL escape.
    """
    codes, seen = [], set()
    current = exc
    for _ in range(4):
        if current is None or id(current) in seen:
            break
        seen.add(id(current))
        code = getattr(current, 'code', None)
        if isinstance(code, str) and code in _WALLET_CODES:
            codes.append(code)
        try:
            codes.extend(_ORACLE_CODE.findall(str(current)[:16384]))
        except Exception:
            pass  # Diagnostics must not replace the original exception.
        current = current.__cause__ or (None if current.__suppress_context__ else current.__context__)
    name = type(exc).__name__
    return {'error_type': name if name in _ERROR_TYPES else 'Exception',
            'error_codes': list(dict.fromkeys(codes))[:8] or ['unclassified']}


def log_database_failure(exc, *, stage, operation, started_at, trace_id=None):
    """Stage/operation must be code-owned names, never request values or SQL."""
    fields = dict(failure_fields(exc), db_stage=stage, db_operation=operation,
                  status='failed', duration_ms=round((perf_counter()-started_at)*1000, 3))
    if trace_id:
        fields['trace_id'] = trace_id
    logger.error('Database diagnostic: stage=%s operation=%s codes=%s',
                 stage, operation, ','.join(fields['error_codes']), extra=fields)


@contextmanager
def database_stage(stage, operation='pool_initialization'):
    started = perf_counter()
    try:
        yield
    except Exception as exc:
        log_database_failure(exc, stage=stage, operation=operation, started_at=started)
        raise
