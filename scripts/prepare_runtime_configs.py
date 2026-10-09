"""Generate role-scoped private JSON artifacts without printing credentials.

Local preparation only: no network, uploads, IAM changes or deployment changes.
Uses explicit allowlists rather than copying a whole .env. Refuses overwrites.
Run only in a private, access-restricted output directory excluded from Git/images.
"""
import argparse
import json
from pathlib import Path
import secrets
from policy_vector_store import read_env

ROOT = Path(__file__).resolve().parents[1]
PREFIX = 'https://inference.generativeai.ap-hyderabad-1.oci.oraclecloud.com/20251112/hostedApplications/'
APP_IDS = {
    'retail': 'ocid1.generativeaihostedapplication.oc1.ap-hyderabad-1.amaaaaaau6bxbdqamjiqz4ig3nom4rrykpceezbasdidgp33xdtlw5uh56ja',
    'supplier': 'ocid1.generativeaihostedapplication.oc1.ap-hyderabad-1.amaaaaaau6bxbdqamkvmc427pmocylcq5ga4jrrycszca6yryrbv5ftcwpxa',
    'mcp': 'ocid1.generativeaihostedapplication.oc1.ap-hyderabad-1.amaaaaaau6bxbdqaa46wstay2jvanct2izu3rdferc2eoc3tmp3jqbsjsm5a',
}
PROJECT = 'ocid1.generativeaiproject.oc1.ap-hyderabad-1.amaaaaaau6bxbdqajs65ko2iq3kb6pcks6wbqzarlhlg5iqgl5tsxi5chpxa'
VECTOR_STORE = 'vs_hyd_xnna6jd4op7n05g2gxiz2j1cenn8s9gicnf9hqppenokvt9f'


def build_configs(env, service_keys):
    required = ['ORACLE_DB_DSN', 'ORACLE_DB_USER', 'ORACLE_DB_PASSWORD',
                'ORACLE_DB_WALLET_PASSWORD', 'OCI_GENAI_API_KEY',
                'OCI_NAMESPACE', 'OCI_BUCKET_NAME', 'OCI_NOTIFICATION_TOPIC_ID']
    if any(not env.get(key) for key in required):
        raise ValueError('Required private settings are missing')
    if set(service_keys) != {'retail', 'supplier'}:
        raise ValueError('Supply exactly retail and supplier service keys')
    if any(not isinstance(v, str) or len(v) < 32 or any(c.isspace() for c in v)
           for v in service_keys.values()) or len(set(service_keys.values())) != 2:
        raise ValueError('Service keys must be distinct and valid')
    mcp_keys = {role: secrets.token_urlsafe(32) for role in ('retail', 'supplier')}
    common = {key: env[key] for key in required if key != 'OCI_GENAI_API_KEY'}
    common.update({
        'APP_ENV': 'development', 'OCI_REGION': 'ap-hyderabad-1',
        'OCI_AUTH_MODE': 'resource_principal', 'HOSTED_PROBE_MODE': True,
        'ORACLE_DB_DRIVER_MODE': 'thick',
        'ORACLE_DB_WALLET_NAMESPACE': env['OCI_NAMESPACE'],
        'ORACLE_DB_WALLET_BUCKET': 'Retail-Inventory-Wallet-private',
        'ORACLE_DB_WALLET_OBJECT_NAME': 'wallet-file-dbWallet_AIMLCOESANDBOX-Nitin.zip',
        'OCI_NOTIFICATION_PUBLISH_ENABLED': env.get('OCI_NOTIFICATION_PUBLISH_ENABLED','false').lower() == 'true',
        'OCI_NOTIFICATION_SHARED_POC_ENABLED': env.get('OCI_NOTIFICATION_SHARED_POC_ENABLED','false').lower() == 'true',
        'ENTERPRISE_AI_RESPONSES_ENABLED': False,
        'ENTERPRISE_AI_CONVERSATION_STATE_ENABLED': False,
        'ENTERPRISE_AI_FILE_SEARCH_ENABLED': False,
        'ENTERPRISE_AI_NL2SQL_ENABLED': False,
        'ENTERPRISE_AI_GUARDRAILS_ENABLED': False,
        'ENTERPRISE_AI_MCP_SAFETY_ENABLED': False,
        'ENTERPRISE_AI_AGENT_REGISTRY_ENABLED': False,
        'ENTERPRISE_AI_MCP_REGISTRY_ENABLED': False,
        'LANGSMITH_TRACING': False,
    })
    ai = {
        'ENTERPRISE_AI_API_MODE': 'responses',
        'ENTERPRISE_AI_AUTH_MODE': 'genai_api_key',
        'ENTERPRISE_AI_RESPONSES_ENABLED': True,
        'ENTERPRISE_AI_ENDPOINT': 'https://inference.generativeai.ap-hyderabad-1.oci.oraclecloud.com/openai/v1',
        'ENTERPRISE_AI_MODEL_ID': 'google.gemini-2.5-flash',
        'ENTERPRISE_AI_PROJECT_ID': PROJECT,
        'OCI_GENAI_API_KEY': env['OCI_GENAI_API_KEY'],
        'ENTERPRISE_AI_FILE_SEARCH_ENABLED': True,
        'ENTERPRISE_AI_VECTOR_STORE_IDS': [VECTOR_STORE],
    }
    configs = {'backend-v1.json': common | {
        'REMOTE_MCP_ENABLED': False,
        'AGENT_ENDPOINTS': {role: PREFIX+APP_IDS[role]+'/actions/invoke' for role in service_keys},
        'AGENT_SERVICE_KEYS': service_keys,
    }}
    for role in ('retail', 'supplier'):
        cfg = common | {
            'AGENT_SERVICE_KEY': service_keys[role],
            'REMOTE_MCP_ENABLED': True,
            'REMOTE_MCP_ENDPOINT': PREFIX+APP_IDS['mcp']+'/actions/invoke',
            'REMOTE_MCP_SERVICE_KEYS': {role: mcp_keys[role]},
        }
        if role == 'retail':
            cfg.update(ai)
            cfg['ENTERPRISE_AI_CONVERSATION_STATE_ENABLED'] = True
        configs[role+'-agent-v1.json'] = cfg
    configs['mcp-v1.json'] = common | ai | {
        'REMOTE_MCP_ENABLED': False, 'REMOTE_MCP_SERVICE_KEYS': mcp_keys,
    }
    return configs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', type=Path, default=ROOT/'.env')
    parser.add_argument('--private-dir', type=Path, default=ROOT/'runtime-config-private')
    args = parser.parse_args()
    directory = args.private_dir.resolve()
    if not directory.is_dir():
        raise ValueError('Create and restrict the private directory first')
    keys = json.loads((directory/'service-keys.local.json').read_text(encoding='utf-8-sig'))
    configs = build_configs(read_env(args.env_file), keys)
    if any((directory/name).exists() for name in configs):
        raise FileExistsError('Configuration already exists; do not regenerate keys implicitly')
    for name, config in configs.items():
        raw = json.dumps(config, indent=2, ensure_ascii=False)+'\n'
        if len(raw.encode('utf-8')) > 65536:
            raise ValueError('Configuration too large')
        with (directory/name).open('x', encoding='utf-8') as target:
            target.write(raw)
        print(json.dumps({'file': name, 'setting_count':len(config), 'created':True}))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'error_type':type(exc).__name__,
                          'message':'Configuration preparation stopped; inspect inputs privately. No secrets printed.'}))
        raise SystemExit(1)
