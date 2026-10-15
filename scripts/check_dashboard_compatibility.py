"""Validate this app against a local Deployment Studio checkout without cloud calls."""
import argparse
import ast
import base64
import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dashboard-root', required=True, type=Path)
    parser.add_argument('--region', default='ap-hyderabad-1')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    dashboard = args.dashboard_root.resolve()
    sys.path.insert(0, str(dashboard / 'backend'))
    from studio.retail_build import validate_contract
    from studio.retail_region_compat import patched_transport, validation_cases

    manifest = validate_contract(json.loads((root / 'deployment/source-build.json').read_text()))
    source = (root / 'backend/app/enterprise_ai/responses_transport.py').read_text(encoding='utf-8')
    patched = patched_transport(source).decode('utf-8')
    # Fail if the dashboard would need to change the source validator's behavior.
    assert ast.dump(ast.parse(source)) == ast.dump(ast.parse(patched)), 'Region validator needs updating'
    preflight = dashboard / 'backend/studio/retail_assets/settings_preflight.py'
    environment = {**os.environ, 'PYTHONPATH': str(root / 'backend'), 'PYTHONDONTWRITEBYTECODE': '1'}
    for service in manifest['services']:
        name = service['name']
        cases = validation_cases(name, args.region)
        encoded = base64.b64encode(json.dumps(cases).encode()).decode('ascii')
        # Load OCI's Windows networking dependencies before the dashboard clears env.
        # Linux image checks execute the dashboard preflight directly.
        runner = 'import oci, runpy, sys; p=sys.argv.pop(1); runpy.run_path(p, run_name="__main__")'
        result = subprocess.run([sys.executable, '-B', '-c', runner, str(preflight), name, encoded],
                                env=environment, text=True, capture_output=True, timeout=90, check=True)
        expected = {'studio_settings_preflight': 1, 'status': 'passed', 'service': name, 'cases': len(cases)}
        assert json.loads(result.stdout.strip()) == expected, f'{name}: dashboard settings preflight failed'
        print(json.dumps({**expected, 'region': args.region, 'credentials': 'synthetic_only'}), flush=True)


if __name__ == '__main__':
    main()
