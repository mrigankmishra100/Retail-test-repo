"""Read-only Docker save archive check. Reports names/hashes, never secret values.

Checks all saved layers (including deleted files) against supplied local secrets
and credential-file/private-key indicators. Not a comprehensive secret/CVE scan.
"""
import argparse
import hashlib
import json
import re
import tarfile
from pathlib import Path, PurePosixPath


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('archives', nargs='+', type=Path)
    parser.add_argument('--secret-json', action='append', default=[], type=Path)
    parser.add_argument('--secret-env', action='append', default=[], type=Path)
    args = parser.parse_args()
    secrets = set()

    def collect(value, sensitive=False):
        if isinstance(value, dict):
            for key, child in value.items():
                collect(child, sensitive or bool(re.search(r'PASSWORD|SECRET|TOKEN|API_KEY|SERVICE_KEY', key, re.I)))
        elif isinstance(value, list):
            for child in value:
                collect(child, sensitive)
        elif sensitive and isinstance(value, str) and len(value) >= 12:
            if value.startswith('{'):
                try:
                    collect(json.loads(value), True)
                    return
                except ValueError:
                    pass
            if 'REPLACE' not in value:
                secrets.add(value.encode())

    for path in args.secret_json:
        collect(json.loads(path.read_text(encoding='utf-8-sig')))
    if args.secret_env:
        from dotenv import dotenv_values
        for path in args.secret_env:
            collect(dict(dotenv_values(path)))
    overlap = max([16384] + [len(value) for value in secrets])
    findings, checked_layers = [], set()
    # Libraries contain PEM header constants and documentation. Require an
    # actual base64 body/footer, not merely a header string, for this indicator.
    private_key = re.compile(
        rb'-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----'
        rb'(?:\r?\n|\\n)[A-Za-z0-9+/=\r\n\\]{64,}'
        rb'-----END (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----')

    def scan(stream, source):
        tail = b''
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                return
            block = tail + chunk
            matched_secret = any(value in block for value in secrets)
            if matched_secret or private_key.search(block):
                findings.append({'source': source, 'reason': 'known_secret_match' if matched_secret else 'private_key_block'})
                return
            tail = block[-overlap:]

    for archive in args.archives:
        with archive.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        with tarfile.open(archive, 'r:*') as outer:
            manifest = json.load(outer.extractfile('manifest.json'))
            for image in manifest:
                scan(outer.extractfile(image['Config']), archive.name + ':image_config')
                for layer in image['Layers']:
                    if layer in checked_layers:
                        continue
                    checked_layers.add(layer)
                    with tarfile.open(fileobj=outer.extractfile(layer), mode='r|*') as inner:
                        for member in inner:
                            if not member.isfile():
                                continue
                            name = PurePosixPath(member.name)
                            if name.name in {'.env', '.en', 'cwallet.sso', 'ewallet.p12'} or 'runtime-config-private' in name.parts:
                                findings.append({'source': archive.name + ':' + member.name, 'reason': 'credential_file_indicator'})
                            scan(inner.extractfile(member), archive.name + ':' + member.name)
        print(json.dumps({'archive': archive.name, 'bytes': archive.stat().st_size,
                          'sha256': digest, 'repo_tags': [tag for entry in manifest for tag in entry.get('RepoTags', [])]}), flush=True)
    print(json.dumps({'unique_layers_scanned': len(checked_layers), 'findings': findings,
                      'scope': 'known local secrets and credential indicators; not a comprehensive scanner'}), flush=True)
    return bool(findings)


if __name__ == '__main__':
    raise SystemExit(main())
