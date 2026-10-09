"""Explicit policy ingestion; never runs at web-app startup.

Inventory is read-only. Ingest requires --store-id and --manifest, downloads
only SUP001..SUP004 policy PDFs from the configured Object Storage bucket,
then uploads them to the existing OCI vector store. No deletes or overwrites.
No inference, notifications, or database changes. Files API resources persist;
record IDs from the manifest for deliberate cleanup by an administrator.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from urllib.error import HTTPError
from urllib.request import Request, build_opener
from uuid import uuid4
from oci_auth_helpers import load_key, NoRedirect, config_signer


def read_env(path):
    values = {}
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        match = re.match(r'^\s*([A-Z][A-Z0-9_]*)\s*=(.*)$', line)
        if match:
            value = match[2].strip()
            if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
                value = value[1:-1]
            values[match[1]] = value
    return values


def opaque_id(value):
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,256}', value or ''):
        raise ValueError('Invalid resource identifier')
    return value


class API:
    def __init__(self, region, project, key=None, *, signer=None):
        if (key is None) == (signer is None):
            raise ValueError('Select exactly one authentication method')
        if not re.fullmatch(r'[a-z]+(?:-[a-z]+)+-\d+', region):
            raise ValueError('Invalid region')
        if not project.startswith('ocid1.generativeaiproject.oc1.'+region+'.'):
            raise ValueError('Project region mismatch')
        self.base = 'https://inference.generativeai.'+region+'.oci.oraclecloud.com/openai/v1'
        self.headers = {'OpenAI-Project':project}
        self.signer = signer
        if key is not None:
            self.headers['Authorization'] = 'Bearer '+key

    def call(self, method, path, payload=None, *, content_type='application/json'):
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode() if payload is not None else None
        headers = self.headers | {'Content-Type':content_type}
        if self.signer is not None:
            from oci._vendor import requests
            prepared = requests.Request(method, self.base+path, data=data, headers=headers).prepare()
            self.signer(prepared)
            headers = dict(prepared.headers)
        request = Request(self.base+path, method=method, data=data, headers=headers)
        try:
            with build_opener(NoRedirect()).open(request, timeout=45) as response:
                raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise ValueError('Oversized result')
                return json.loads(raw)
        except HTTPError as error:
            status, request_id = error.code, error.headers.get('opc-request-id')
            error.close()
            print(json.dumps({'operation':method+' '+path.split('?')[0],
                              'status':status,'opc_request_id':request_id,
                              'timestamp':datetime.now(timezone.utc).isoformat()}))
            raise RuntimeError('OCI resource request failed; no automatic retry') from None

    def upload(self, name, raw):
        boundary = 'policy-'+uuid4().hex
        data = (f'--{boundary}\r\nContent-Disposition: form-data; name="purpose"\r\n\r\nassistants\r\n'
                f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
                'Content-Type: application/pdf\r\n\r\n').encode()+raw+f'\r\n--{boundary}--\r\n'.encode()
        return self.call('POST','/files', data, content_type='multipart/form-data; boundary='+boundary)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['inventory','ingest','status'])
    p.add_argument('--project-id', required=True)
    p.add_argument('--region', default='ap-hyderabad-1')
    p.add_argument('--env-file', type=Path, default=Path(__file__).resolve().parents[1]/'.env')
    p.add_argument('--store-id')
    p.add_argument('--manifest', type=Path)
    p.add_argument('--auth', choices=['api-key','oci-config'], default='api-key')
    p.add_argument('--config-file', type=Path, help='Defaults to OCI_CONFIG_FILE in .env or ~/.oci/config')
    p.add_argument('--profile', help='Defaults to OCI_CONFIG_PROFILE in .env or DEFAULT')
    args = p.parse_args()
    env = read_env(args.env_file)
    config_file = args.config_file or Path(env.get('OCI_CONFIG_FILE','~/.oci/config'))
    profile = args.profile or env.get('OCI_CONFIG_PROFILE','DEFAULT')
    signer = config_signer(config_file, profile) if args.auth == 'oci-config' else None
    api = API(args.region, args.project_id,
              load_key(args.env_file) if args.auth == 'api-key' else None, signer=signer)
    if args.action == 'inventory':
        result = api.call('GET','/vector_stores?limit=100')
        print(json.dumps({'stores':[{'id':r['id'],'name':r.get('name'),'status':r.get('status')}
                                   for r in result.get('data',[])], 'has_more':result.get('has_more')}))
        return
    store = opaque_id(args.store_id)
    if args.action == 'status':
        result = api.call('GET','/vector_stores/'+store+'/files?limit=100')
        print(json.dumps({'files':[{'id':r['id'],'status':r.get('status')}
                                   for r in result.get('data',[])], 'has_more':result.get('has_more')}))
        return
    if not args.manifest:
        p.error('Ingest requires an explicit new --manifest path')
    # Reserve a new audit file before any upload; never overwrite existing runs.
    with args.manifest.open('x', encoding='utf-8') as manifest:
        manifest.write(json.dumps({'store_id':store, 'event':'started'})+'\n')
        manifest.flush()
        import oci
        config = oci.config.from_file(str(config_file.expanduser()), profile)
        config['region'] = args.region
        storage = oci.object_storage.ObjectStorageClient(config, timeout=(10,20),
            signer=signer or config_signer(config_file, profile),
            retry_strategy=oci.retry.NoneRetryStrategy())
        try:
            for supplier in ('SUP001','SUP002','SUP003','SUP004'):
                name = supplier+'_policy.pdf'
                obj = storage.get_object(env['OCI_NAMESPACE'], env['OCI_BUCKET_NAME'], name)
                try:
                    raw = obj.data.raw.read(2_000_001)
                finally:
                    obj.data.close()
                if len(raw)>2_000_000 or not raw.startswith(b'%PDF-'):
                    raise ValueError('Invalid policy PDF')
                uploaded = api.upload(name,raw)
                file_id = opaque_id(uploaded['id'])
                record = {'name':name,'sha256':hashlib.sha256(raw).hexdigest(), 'file_id':file_id}
                manifest.write(json.dumps(record)+'\n'); manifest.flush()
                attached = api.call('POST','/vector_stores/'+store+'/files', {'file_id':file_id})
                manifest.write(json.dumps({'file_id':file_id,'attachment_status':attached.get('status')})+'\n')
                manifest.flush()
                print(json.dumps({'name':name,'file_id':file_id,'status':attached.get('status')}))
        finally:
            storage.base_client.session.close()


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'error_type':type(exc).__name__, 'message':'Operation did not complete. Do not blindly repeat ingestion; inspect the audit manifest.'}))
        raise SystemExit(1)
