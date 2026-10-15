"""Offline contract checks for Deployment Studio source builds."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICES = ('mcp-server', 'retail-agent', 'supplier-agent', 'backend', 'frontend')


def test_dashboard_source_manifest_and_portable_images():
    manifest = json.loads((ROOT / 'deployment/source-build.json').read_text())
    assert manifest == {'schema_version': 1, 'application': 'retail-inventory', 'platform': 'linux/amd64',
        'services': [{'name': name, 'context': '.',
            'dockerfile': 'frontend/Dockerfile' if name == 'frontend' else 'backend/Dockerfile.source',
            'target': None if name == 'frontend' else name} for name in SERVICES]}
    for service in manifest['services']:
        dockerfile = (ROOT / service['dockerfile']).read_text()
        assert 'hyd.ocir.io' not in dockerfile
        assert 'org.opencontainers.image.revision' in dockerfile
        assert 'USER appuser' in dockerfile and 'APT::Update::Error-Mode=any' in dockerfile
        assert 'CHAT_METRICS_PATH=/tmp/chat-metrics.sqlite3' in dockerfile
        if service['target']:
            assert 'AS ' + service['target'] in dockerfile
    assert (ROOT / 'backend/docker/oracle-instantclient.conf').read_text().strip() == '/opt/oracle/instantclient_23_26'


def test_source_packaging_excludes_credentials_and_legacy_images():
    ignore = (ROOT / '.dockerignore').read_text()
    for pattern in ('docker-images/', 'runtime-config-private/', '**/*.pem', '**/.env', 'runtime/', '**/*.egg-info/'):
        assert pattern in ignore
    assert '/docker-images export-ignore' in (ROOT / '.gitattributes').read_text()


def test_notification_probe_installed_on_all_runtime_entrypoints():
    for name in ('main.py', 'agent_app.py', 'mcp/server.py'):
        assert 'studio_notification_probe' in (ROOT / 'backend/app' / name).read_text()
