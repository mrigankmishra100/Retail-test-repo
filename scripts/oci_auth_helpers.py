"""Credential loading for opt-in operational scripts; never prints secret values."""
from pathlib import Path
import re
from urllib.request import HTTPRedirectHandler


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def load_key(path):
    values = []
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        match = re.match(r'^\s*OCI_GENAI_API_KEY\s*=(.*)$', line)
        if match:
            value = match[1].strip()
            if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
                value = value[1:-1]
            values.append(value)
    if len(values) != 1 or not values[0] or re.search(r'\s', values[0]):
        raise ValueError('A single populated API-key assignment is required')
    return values[0]


def config_signer(config_file, profile):
    import oci
    config = oci.config.from_file(str(config_file.expanduser()), profile)
    if config.get('security_token_file'):
        token = Path(config['security_token_file']).expanduser().read_text().strip()
        private_key = oci.signer.load_private_key_from_file(
            str(Path(config['key_file']).expanduser()), config.get('pass_phrase'))
        return oci.auth.signers.SecurityTokenSigner(token, private_key)
    oci.config.validate_config(config)
    return oci.signer.Signer(tenancy=config['tenancy'], user=config['user'],
        fingerprint=config['fingerprint'],
        private_key_file_location=str(Path(config['key_file']).expanduser()),
        pass_phrase=config.get('pass_phrase'))
