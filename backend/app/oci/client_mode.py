"""Process-wide native Oracle initialization, performed lazily, never at import."""
from threading import Lock

_lock = Lock()
_initialized = False


def initialize_thick_client(driver, config_dir):
    """Load native libraries once, without binding them to one pool's wallet.

    Linux resolves native libraries through ldconfig; no user-provided lib_dir.
    Each pool parses its own TNS file and supplies wallet_location in its connect
    descriptor. Readiness and API pools intentionally have independent lifetimes.
    Native initialization errors propagate to the existing redacted logger.
    """
    global _initialized
    if not config_dir:
        raise ValueError('Thick mode requires a wallet configuration directory')
    with _lock:
        if not _initialized:
            driver.init_oracle_client()
            if driver.is_thin_mode():
                raise RuntimeError('Native Oracle client initialization did not enable Thick mode')
            _initialized = True
