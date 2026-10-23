"""Prepare a NEW private Retail runtime configuration with a shared OpenAI key.

No network calls or secret values printed. Does not modify deployment state.
"""
import argparse
import json
from pathlib import Path
import re


def read_key(path):
    value = path.read_text(encoding='utf-8-sig').strip()
    match = re.fullmatch(r'''(?:[A-Za-z _-]{1,32}\s*[:=]\s*)?["']?(sk-[A-Za-z0-9_-]{9,509})["']?''', value)
    if not match:
        raise ValueError('Key file must contain only an OpenAI API key, or OPENAI_API_KEY=<key>')
    return match.group(1)


def configure(config, key):
    if not isinstance(config, dict):
        raise ValueError('The service configuration must be a JSON object')
    role = next((v for k, v in config.items() if k.upper() == 'APPLICATION_ROLE'), None)
    if role != 'retail-agent':
        raise ValueError('Use the Retail agent runtime JSON for hosted OpenAI configuration')
    result = {k: v for k, v in config.items() if k.upper() != 'OPENAI_API_KEY'}
    result['OPENAI_API_KEY'] = key
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--config', type=Path, help='Retail agent private runtime JSON')
    source.add_argument('--env-file', type=Path, help='Local backend environment file')
    parser.add_argument('--key-file', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    key = read_key(args.key_file)
    if args.config:
        content = json.dumps(configure(json.loads(args.config.read_text(encoding='utf-8-sig')), key), indent=2) + '\n'
    else:
        lines = args.env_file.read_text(encoding='utf-8-sig').splitlines()
        lines = [line for line in lines if not re.match(r'^\s*(?:export\s+)?OPENAI_API_KEY\s*=', line, re.I)]
        content = '\n'.join(lines) + '\nOPENAI_API_KEY=' + key + '\n'
    # Exclusive creation protects both deployment receipts and existing settings.
    with args.output.open('x', encoding='utf-8') as output:
        output.write(content)
    print('Prepared private configuration. Restart the local backend or apply it to the Retail agent runtime.')


if __name__ == '__main__':
    main()
