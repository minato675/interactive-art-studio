import json
import sys
from pathlib import Path

import pytest
from test_head_automation import automatic, decision, start, state, tick  # noqa: F401 — fixture
from test_runtime_kit import kit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'infra'))
from generation_worker import run_one
from ue_worker import validate_head_template


def test_generation_handler_downloads_reference_and_reports_without_secrets(automatic, tmp_path):  # noqa: F811
    api, settings, _ = automatic
    workflow = start(api)
    decision(api, settings, workflow)
    handler = tmp_path/'handler.py'
    handler.write_text('''import argparse,json
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--request');p.add_argument('--output');a=p.parse_args()
task=json.loads(Path(a.request).read_text('utf-8'))
assert 'token' not in task and 'token' not in task['generation']
if task['kind']=='assets_generate':
 parent=task['inputs'][0]
 assert (Path(task['input_root'])/parent['id']/parent['result']['files'][0]['name']).is_file()
name=task['generation']['conditions']['output_name']
(Path(a.output)/name).write_bytes(bytes.fromhex('89504e470d0a1a0a')+b'fixture')
''',encoding='utf-8')
    config={'handler':[sys.executable,str(handler)],'working_directory':str(tmp_path),
            'cache':str(tmp_path/'cache'),'timeout_seconds':5}
    client=kit(api,'gen',tmp_path/'bg-kit')
    assert run_one(client,config)['event']=='result_registered'
    tick(api,settings)
    assert state(api,workflow)['status']=='planning_assets'
    decision(api,settings,workflow)
    client=kit(api,'a1',tmp_path/'asset-kit')
    assert run_one(client,config)['event']=='result_registered'
    assert not (client.folder/'assignment.secret.json').exists()


def test_ue_worker_rejects_changed_pinned_template():
    expected={'template':'gpu_particles','fingerprint':'a'*64}
    job={'node_id':'pc1','payload':{'assembly':{'target_node':'pc1'},'_head':{'template':expected}}}
    validate_head_template(job,{'templates':[expected]})
    with pytest.raises(ValueError,match='fingerprint'):
        validate_head_template(job,{'templates':[{**expected,'fingerprint':'b'*64}]})
    job['node_id']='other'
    with pytest.raises(ValueError,match='target'):
        validate_head_template(job,{'templates':[expected]})
    assert json.dumps(expected)
