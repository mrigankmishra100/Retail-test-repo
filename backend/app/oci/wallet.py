"""Bounded, read-only wallet retrieval into an owner-only temporary directory."""
from io import BytesIO
import os
from pathlib import Path, PurePosixPath
import stat
import tempfile
from threading import Lock
from zipfile import ZipFile

from app.oci.storage import create_object_storage_client

MAX_ZIP_BYTES = 4 * 1024 * 1024
MAX_EXPANDED_BYTES = 8 * 1024 * 1024
MAX_FILE_BYTES = 2 * 1024 * 1024
REQUIRED_FILES = frozenset({'tnsnames.ora', 'ewallet.pem'})
THICK_REQUIRED_FILES = frozenset({'tnsnames.ora', 'cwallet.sso'})


class WalletLoadError(RuntimeError):
    """Only a fixed safe code may cross this credential boundary."""
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def wallet_files(payload, driver_mode='thin'):
    """Never extract archive-controlled paths or execute archive sqlnet directives."""
    if driver_mode not in {'thin', 'thick'}:
        raise ValueError('Unsupported database driver mode')
    required = THICK_REQUIRED_FILES if driver_mode == 'thick' else REQUIRED_FILES
    if not payload or len(payload) > MAX_ZIP_BYTES:
        raise WalletLoadError('wallet_size_invalid')
    try:
        with ZipFile(BytesIO(payload)) as archive:
            entries = archive.infolist()
            if len(entries) > 64 or sum(e.file_size for e in entries) > MAX_EXPANDED_BYTES:
                raise WalletLoadError('wallet_archive_limits_exceeded')
            chosen = {}
            for entry in entries:
                name = entry.filename
                parts = PurePosixPath(name).parts
                mode = (entry.external_attr >> 16) & 0xFFFF
                if (not parts or name.startswith('/') or '\\' in name or ':' in name
                        or '..' in parts or '\x00' in name or len(name) > 256
                        or (stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR))):
                    raise WalletLoadError('wallet_archive_path_invalid')
                if entry.flag_bits & 1:
                    raise WalletLoadError('encrypted_zip_not_supported')
                if entry.is_dir() or parts[-1] not in required:
                    continue
                basename = parts[-1]
                if basename in chosen or entry.file_size > MAX_FILE_BYTES:
                    raise WalletLoadError('wallet_archive_ambiguous_or_oversized')
                with archive.open(entry) as stream:
                    content = stream.read(MAX_FILE_BYTES + 1)
                if not content or len(content) > MAX_FILE_BYTES:
                    raise WalletLoadError('wallet_file_size_invalid')
                chosen[basename] = content
            if chosen.keys() != required:
                raise WalletLoadError('wallet_required_files_missing')
            if driver_mode == 'thin' and b'-----BEGIN ' not in chosen['ewallet.pem']:
                raise WalletLoadError('wallet_pem_invalid')
            chosen['tnsnames.ora'].decode('utf-8')
            return chosen
    except WalletLoadError:
        raise
    except Exception:
        raise WalletLoadError('wallet_archive_invalid') from None


class ObjectStorageWallet:
    """Downloads on first DB connection, once per pool lifecycle, never at import."""
    def __init__(self, *, namespace, bucket, object_name, auth_mode='resource_principal',
                 region=None, config_file='~/.oci/config', config_profile='DEFAULT', temp_root=None,
                 driver_mode='thin'):
        if driver_mode not in {'thin', 'thick'}:
            raise ValueError('Unsupported database driver mode')
        self.driver_mode = driver_mode
        self.namespace, self.bucket, self.object_name = namespace, bucket, object_name
        self._sdk_options = dict(auth_mode=auth_mode, region=region,
                                 config_file=config_file, config_profile=config_profile)
        self._temp_root = temp_root
        self._temporary = None
        self._lock = Lock()

    def _download(self):
        client = None
        try:
            client = create_object_storage_client(**self._sdk_options)
            response = client.get_object(self.namespace, self.bucket, self.object_name)
            try:
                payload = response.data.raw.read(MAX_ZIP_BYTES + 1)
            finally:
                response.data.close()
            if len(payload) > MAX_ZIP_BYTES:
                raise WalletLoadError('wallet_size_invalid')
            return payload
        except WalletLoadError:
            raise
        except Exception as exc:
            code = {401: 'wallet_authentication_failed', 403: 'wallet_access_denied',
                    404: 'wallet_object_not_found'}.get(getattr(exc, 'status', None), 'wallet_download_failed')
            raise WalletLoadError(code) from None
        finally:
            if client is not None:
                try:
                    client.base_client.session.close()
                except Exception:
                    pass  # Never replace a safe wallet failure with raw SDK cleanup details.

    def prepare(self):
        with self._lock:
            if self._temporary is not None:
                return self._temporary.name
            files = wallet_files(self._download(), self.driver_mode)
            temporary = None
            try:
                temporary = tempfile.TemporaryDirectory(prefix='retail-db-wallet-', dir=self._temp_root)
                os.chmod(temporary.name, 0o700)
                if self.driver_mode == 'thick':
                    # Replace wallet ZIP's platform-specific path with our private
                    # runtime path. Never copy arbitrary sqlnet directives from ZIP.
                    directory = Path(temporary.name).as_posix()
                    if any(c in directory for c in '\"\r\n()'):
                        raise ValueError('Unsupported wallet directory')
                    files['sqlnet.ora'] = (
                        'WALLET_LOCATION=(SOURCE=(METHOD=file)(METHOD_DATA=(DIRECTORY="'
                        + directory + '")))\nSSL_SERVER_DN_MATCH=yes\n'
                    ).encode('utf-8')
                for name, content in files.items():
                    descriptor = os.open(Path(temporary.name) / name,
                                         os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(descriptor, 'wb') as stream:
                        stream.write(content)
                self._temporary = temporary
                return temporary.name
            except Exception:
                if temporary is not None:
                    temporary.cleanup()
                raise WalletLoadError('wallet_materialization_failed') from None

    def close(self):
        with self._lock:
            if self._temporary is not None:
                self._temporary.cleanup()
                self._temporary = None
