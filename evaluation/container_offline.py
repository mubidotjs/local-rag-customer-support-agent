"""Byte-only preservation and rendered Compose checks. No model access."""
import argparse
import hashlib
import json
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[1]
SNAPSHOT_SHA='4892195abd9567d09543dfec7d0c0148f0a0dce8dec240565eca17d8a8c8cafb'

def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()

def preserved(require_local=False):
    path=ROOT/'reports/container-offline/preservation.json'
    if sha(path)!=SNAPSHOT_SHA: raise ValueError('preservation_inventory_changed')
    snapshot=json.loads(path.read_text()); checked=0; skipped=[]
    for name,expected in snapshot['files'].items():
        p=ROOT/name
        if not p.exists() and not require_local and name in snapshot['optional_in_fresh_ci']:
            skipped.append(name); continue
        if sha(p) not in {expected,snapshot['git_representations'].get(name,expected)}: raise ValueError('historical_file_changed:'+name)
        checked+=1
    for name in snapshot['required_absent']:
        if (ROOT/name).exists() or (ROOT/name).is_symlink(): raise ValueError('historical_absence_changed')
    return {'protected_files':len(snapshot['files']),'verified_available':checked,'skipped_optional':skipped}

def compose_invariants(value):
    services=value['services']; assert set(services)=={'frontend','backend'} or set(services)=={'frontend','backend','maintenance'}
    frontend,backend=services['frontend'],services['backend']
    assert backend['network_mode']=='service:frontend' and not backend.get('ports') and not backend.get('extra_hosts')
    assert frontend['ports']==[{'mode':'ingress','host_ip':'127.0.0.1','target':8080,'published':'5173','protocol':'tcp'}]
    assert all(v.get('internal') for v in value['networks'].values())
    for name,s in services.items():
        assert s['user'] in {'101:101','10001:10001'} and s['read_only'] is True and s['cap_drop']==['ALL']
        assert s['security_opt']==['no-new-privileges:true'] and not s.get('privileged') and not s.get('devices')
        assert 0<float(s['cpus'])<=1 and 0<int(s['mem_limit'])<=805306368 and 0<s['pids_limit']<=64
        assert s['restart'] in {'on-failure:3','no'} and all('size=16m' in t for t in s['tmpfs'])
        for mount in s.get('volumes',[]):
            assert mount['type']=='bind' and not mount.get('bind',{}).get('create_host_path',False)
            assert mount['target'] in {'/var/lib/rag','/run/ollama-relay'}
            if name!='maintenance': assert mount['read_only']
        assert not s.get('env_file') and not s.get('pid')
    if 'maintenance' in services: assert services['maintenance']['network_mode']=='none'
    for name in ('backend','frontend'):
        text=(ROOT/f'containers/{name}.Dockerfile').read_text()
        bases=re.findall(r'^FROM (\S+)',text,re.M)
        assert bases and all(re.fullmatch(r'[^ ]+@sha256:[0-9a-f]{64}',b) for b in bases)
        assert 'USER ' in text and 'HEALTHCHECK ' in text and 'STOPSIGNAL ' in text
    return {'status':'PASS','published_ports':['127.0.0.1:5173'],'model_requests':0}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compose',type=Path)
    parser.add_argument('--require-local',action='store_true')
    args=parser.parse_args()
    result=preserved(args.require_local)
    if args.compose: result['compose']=compose_invariants(json.loads(args.compose.read_text(encoding='utf-8-sig')))
    print(json.dumps(result,indent=2))

if __name__=='__main__': main()
