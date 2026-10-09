"""Repository deployment artifacts: no OCI credentials or cloud operations required."""
import importlib.util
import json
from pathlib import Path
import threading
from urllib.request import Request, urlopen

ROOT=Path(__file__).resolve().parents[1]

def test_calculator_has_both_hosted_probes_and_browser_api():
    root=ROOT/'examples/calculator'
    recipe=json.loads((root/'app-deployment.json').read_text())
    web=recipe['services']['web']
    assert web['port']==8080 and web['health_path']=='/health' and web['readiness_path']=='/ready'
    spec=importlib.util.spec_from_file_location('deployment_calculator',root/'server.py')
    app=importlib.util.module_from_spec(spec);spec.loader.exec_module(app)
    server=app.create_server('127.0.0.1',0)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base='http://127.0.0.1:'+str(server.server_port)
    try:
        for path in ['/health','/ready']:
            with urlopen(base+path,timeout=5) as response:
                assert response.status==200 and response.headers.get_content_type()=='application/json'
        with urlopen(base+'/',timeout=5) as response: assert b'Little Calculator' in response.read()
        req=Request(base+'/api/calculate',data=b'{"a":"12","b":"8","operation":"add"}',headers={'Content-Type':'application/json'})
        with urlopen(req,timeout=5) as response:assert json.load(response)['result']=='20'
    finally:
        server.shutdown();server.server_close();thread.join(timeout=5)

def test_retail_plan_matches_saved_images_and_schema():
    raw=(ROOT/'manifest.json').read_text();plan=json.loads(raw)
    inventory=json.loads((ROOT/'docker-images/manifest.json').read_text())
    images={x['component']:x for x in inventory['images']}
    provenance=json.loads((ROOT/'deployment/notification-checks-20261009.json').read_text())
    assert plan['release']==inventory['release']==provenance['release']
    assert plan['image_inventory']=='docker-images/manifest.json'
    assert plan['image_source_mode']=='saved_archives'
    updated={x['service']:x for x in provenance['image_changes']}
    for name,item in updated.items():
        assert images[name]['archive_sha256']==item['archive_sha256']
    assert plan['deployment_status']=='planned' and 'database-schema-initialization' in plan['blockers']
    assert set(plan['services'])==set(images)=={'backend','retail-agent','supplier-agent','mcp-server','frontend'}
    assert plan['authentication']=='NO_AUTH_CONFIG'
    for name,service in plan['services'].items():
        assert service['image']['sha256']==images[name]['archive_sha256']
        assert service['image']['archive']=='docker-images/'+images[name]['archive']
        assert service['port']==8080 and service['readiness_path']=='/ready'
        assert all(dep in plan['services'] for dep in service['depends_on'])
    completed=set()
    while len(completed)<len(plan['services']):
        ready={name for name,service in plan['services'].items() if name not in completed and set(service['depends_on'])<=completed}
        assert ready,'Dependency cycle'
        completed |= ready
    for filename in plan['database_initialization']['sql_files']:
        assert (ROOT/filename).is_file()
    assert not any('migrations/' in x for x in plan['database_initialization']['sql_files'])
    assert len(set(plan['generated_secrets']))==4
    assert 'ocid1.' not in raw and 'ax4qsxvnsmtm' not in raw and 'PRIVATE KEY' not in raw
    assert plan['optional_ai']['enabled'] is False
