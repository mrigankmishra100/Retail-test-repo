"""Enable self-hosted tracing in a NEW private server runtime JSON file.

No network calls, uploads, image changes or secret values printed.
"""
import argparse
import json
from pathlib import Path
import re
from urllib.parse import urlsplit


def configure(config, values):
    required = ('LANGFUSE_BASE_URL', 'LANGFUSE_PUBLIC_KEY', 'LANGFUSE_SECRET_KEY')
    if any(not isinstance(values.get(key), str) or not values[key].strip() for key in required):
        raise ValueError('Supply LANGFUSE_BASE_URL, LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY')
    url = urlsplit(values['LANGFUSE_BASE_URL'])
    if (url.scheme not in {'http', 'https'} or not url.hostname or url.username is not None
            or url.password is not None or url.query or url.fragment):
        raise ValueError('Use an HTTP(S) Langfuse URL without credentials, query or fragment')
    environment = values.get('LANGFUSE_ENVIRONMENT', 'poc')
    if not re.fullmatch(r'[a-z0-9_-]{1,40}', environment) or environment.startswith('langfuse'):
        raise ValueError('Use a lowercase Langfuse environment name')
    if not isinstance(config, dict):
        raise ValueError('The service configuration must be a JSON object')
    # Remove case-insensitive duplicates accepted by the settings loader.
    result = {key: value for key, value in config.items()
              if not key.upper().startswith('LANGFUSE_') and key.upper() != 'LANGSMITH_TRACING'}
    result.update({key: values[key].strip() for key in required})
    result.update(LANGFUSE_TRACING_ENABLED=True, LANGSMITH_TRACING=False,
                  LANGFUSE_ENVIRONMENT=environment)
    for key in ('LANGFUSE_RELEASE', 'LANGFUSE_HTTP_PROXY'):
        if values.get(key):
            result[key] = values[key]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--langfuse-env', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--service', required=True,
                        choices=['backend', 'retail-agent', 'supplier-agent', 'mcp-server'])
    args = parser.parse_args()
    from dotenv import dotenv_values
    result = configure(json.loads(args.config.read_text(encoding='utf-8-sig')),
                       dotenv_values(args.langfuse_env))
    # Exclusive create: never silently replace a deployment configuration.
    with args.output.open('x', encoding='utf-8') as output:
        json.dump(result, output, indent=2)
        output.write('\n')
    print('Prepared private server configuration. Upload through the existing deployment configuration flow and restart the service.')


if __name__ == '__main__':
    main()
