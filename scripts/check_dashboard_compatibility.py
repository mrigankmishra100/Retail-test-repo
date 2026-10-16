"""Validate this app against a local Deployment Studio checkout without cloud calls."""
import argparse
import ast
import base64
import json
import os
import shlex
from pathlib import Path
import subprocess
import sys


def image_defaults(dockerfile):
    """Read the source image's inherited ENV contract, without running Docker."""
    stages, current = {}, None
    for line in dockerfile.replace('\\\n', ' ').splitlines():
        if not line.startswith(('FROM ', 'ENV ')):
            continue
        parts = shlex.split(line)
        if parts and parts[0] == 'FROM' and 'AS' in parts:
            current = parts[-1]
            stages[current] = dict(stages.get(parts[1], {}))
        elif parts and parts[0] == 'ENV' and current:
            stages[current].update(part.split('=', 1) for part in parts[1:])
    return stages


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dashboard-root', required=True, type=Path)
    parser.add_argument('--region', default='ap-hyderabad-1')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    graph_source = ast.parse((root / 'backend/app/agents/retail_conversation.py').read_text(encoding='utf-8'))
    limits = {node.targets[0].id: ast.literal_eval(node.value) for node in graph_source.body
              if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
              and node.targets[0].id in {'MAX_MODEL_CALLS', 'MAX_TOOL_CALLS'}}
    assert limits == {'MAX_MODEL_CALLS': 12, 'MAX_TOOL_CALLS': 24}
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
    defaults = image_defaults((root / 'backend/Dockerfile.source').read_text())
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
        if name == 'retail-agent':
            # Studio's preflight clears Docker ENV. Also check the effective runtime
            # combination so an omitted recipe field cannot silently lose our defaults.
            runtime_probe = '''import oci, os, sys, json
os.environ.clear()
os.environ.update(json.loads(sys.argv[1]), RETAIL_ENV_FILE='/nonexistent/parity.env', APP_CONFIG_JSON=sys.argv[2])
from app.config import Settings
from app.enterprise_ai.guardrails import EnterpriseAIGuardrailsClient
s = Settings()
assert s.prompt_guardrails_enabled
assert not EnterpriseAIGuardrailsClient.from_settings(s).custom_rules_enabled
assert s.oci_auth_mode == 'resource_principal'
assert all(p.input_usd_per_million is not None and p.output_usd_per_million is not None and p.price_version for p in s.chat_model_profiles)
print('runtime_prompt_checks_and_estimates_passed')
'''
            for case in cases:
                probe = subprocess.run([sys.executable, '-B', '-c', runtime_probe,
                    json.dumps(defaults[name]), json.dumps(case['settings'])], env=environment,
                    text=True, capture_output=True, timeout=90, check=True)
                assert probe.stdout.strip().splitlines()[-1] == 'runtime_prompt_checks_and_estimates_passed'
            print(json.dumps({'service': name, 'runtime_parity': 'passed', 'cases': len(cases)}), flush=True)


if __name__ == '__main__':
    main()
