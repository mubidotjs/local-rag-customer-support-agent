"""Mock Docker only. All claims/workspaces are under temporary fictional roots."""
from copy import deepcopy
from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import sys
import time
import pytest
from evaluation import container_runtime as run

REAL_ROOT=Path(__file__).resolve().parents[1]
IDS={'frontend':'f'*64,'backend':'b'*64}
IMAGES={'frontend':'sha256:'+'e'*64,'backend':'sha256:'+'a'*64}
NETWORK='c'*64
NATIVE_COMMAND=run.Native.command
NATIVE_HTTP=run.Native.http

# Captured via read-only inspect of the unchanged approved image IDs.
BACKEND_ENV=[
    'PATH=/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
    'LANG=C.UTF-8','GPG_KEY=7169605F62C751356D054A26A821E680E5FA6305',
    'PYTHON_VERSION=3.13.18',
    'PYTHON_SHA256=abb0102e6d4ae027509bcb82327d9dd85515b44a3ba8ad538cfc5e9847029f68',
    'PYTHONPATH=/runtime:/app','PYTHONDONTWRITEBYTECODE=1','PYTHONUNBUFFERED=1','PYTHONUTF8=1',
]
FRONTEND_ENV=[
    'PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
    'NGINX_VERSION=1.30.6','PKG_RELEASE=1','DYNPKG_RELEASE=1','NJS_VERSION=1.0.2','NJS_RELEASE=1',
]

class FakeDocker:
    def __init__(self,root=None,mode=None): self.root=root; self.mode=mode; self.calls=[]; self.http_calls=[]; self.running=False; self.restart=False
    def image_info(self):
        p=run.plan()
        return [{'Id':IMAGES[role],'Os':'linux','Architecture':'amd64','Config':{'User':'10001:10001' if role=='backend' else '101:101',
            'Entrypoint':['python','-m','support_agent.production'] if role=='backend' else ['nginx','-g','daemon off;'],
            'Labels':{'org.project5.source':run.BASELINE,'org.project5.context':p['build_context_sha256']},'Healthcheck':{'Test':['CMD','fixture']},'Env':list(BACKEND_ENV if role=='backend' else FRONTEND_ENV)}}
            for role in ('backend','frontend')]
    def info(self):
        return {'ID':run.plan()['engine_id'],'DefaultRuntime':'runc','Architecture':'x86_64','SecurityOptions':['name=seccomp'],
                'MemTotal':8_000_000_000,'MemoryLimit':True,'PidsLimit':True,'CpuCfsQuota':True,'CpuCfsPeriod':True,'NCPU':4,'CgroupVersion':'2'}
    def containers(self):
        values=[]
        for role in ('frontend','backend'):
            b=role=='backend'
            values.append({'Id':IDS[role],'Image':IMAGES[role],'Config':{'Labels':{'org.project5.run':run.RUN_ID},'User':'10001:10001' if b else '101:101','Env':['RAG_MODEL_MODE=disabled'] if b else []},
                'HostConfig':{'ReadonlyRootfs':True,'Privileged':False,'CapAdd':None,'CapDrop':['ALL'],'SecurityOpt':['no-new-privileges:true'],'Init':True,
                    'NanoCpus':1_000_000_000 if b else 250_000_000,'Memory':805306368 if b else 134217728,'MemoryReservation':268435456 if b else 33554432,
                    'PidsLimit':64 if b else 32,'RestartPolicy':{'Name':'on-failure','MaximumRetryCount':3},'NetworkMode':'container:'+IDS['frontend'] if b else NETWORK,
                    'PortBindings':{} if b else {'8080/tcp':[{'HostIp':'127.0.0.1','HostPort':'5173'}]}},
                'State':{'Running':self.running,'ExitCode':0,'StartedAt':'after' if self.restart and b else 'before'},'RestartCount':0,
                'NetworkSettings':{'Networks':{'isolated':{'NetworkID':NETWORK}}} if not b else {'Networks':{}},
                'Mounts':[{'Type':'bind','Destination':'/var/lib/rag','RW':False}] if b else []})
        if self.mode=='uid': values[1]['Config']['User']='0:0'
        if self.mode=='mount': values[1]['Mounts'][0]['RW']=True
        if self.mode=='network': values[0]['HostConfig']['PortBindings']['8080/tcp'][0]['HostIp']='0.0.0.0'
        if self.mode=='restart-loop': values[1]['RestartCount']=1
        if self.mode=='startup': values[1]['State']['Running']=False
        if self.mode=='shutdown' and not self.running: values[1]['State']['ExitCode']=137
        return values
    def command(self,args,seconds):
        assert 0<seconds<=20
        if self.root and (self.root/'reports'/run.RUN_ID/'actions.jsonl').exists():
            last=json.loads((self.root/'reports'/run.RUN_ID/'actions.jsonl').read_text().splitlines()[-1])
            assert last['event']=='action_attempt' and last['argv']==args
        self.calls.append(args)
        if self.mode=='timeout' and args[0]=='start': raise run.StopRun('fake_timeout')
        if args==['context','show']: return 'default'
        if args[:2]==['context','inspect']: return json.dumps([{'Endpoints':{'docker':{'Host':'unix:///var/run/docker.sock'}}}])
        if args[0]=='version': return json.dumps({'Server':{'Os':'linux','Arch':'amd64'}})
        if args[0]=='info': return json.dumps(self.info())
        if args[:2]==['image','inspect']: return json.dumps(self.image_info())
        if args[0]=='ps' or args[:2]==['network','ls']: return ''
        if args[:2]==['network','create']: return '--untrusted-output' if self.mode=='bad-id' else NETWORK
        if args[0]=='create':
            assert '--pull=never' in args and '--runtime=runc' in args and '--read-only' in args
            assert not any('host.docker.internal' in a for a in args)
            return IDS['backend'] if '--mount' in args else IDS['frontend']
        if args[0]=='start': self.running=True; return args[1]
        if args[0]=='restart':
            if self.restart: raise AssertionError('Automatic restart retry forbidden')
            self.restart=True; return args[-1]
        if args[0]=='inspect':
            values=self.containers()
            return json.dumps([v for v in values if v['Id'] in args[1:]])
        if args[0]=='exec':
            assert self.root is not None
            fixture=json.loads((self.root/'reports'/run.RUN_ID/'fixture.json').read_text())
            probe={k:fixture[k] for k in ('index_sha256','data_identity','database_identity')}
            probe.update(uid=10001,gid=10001,data_uid=10001,data_gid=10001,data_mode=0o700,mode='disabled',model_socket_exists=False,
                mutation_rejected=True,root_readonly=True,data_readonly=True)
            if self.mode=='socket': probe['model_socket_exists']=True
            if self.mode=='permissions': probe['data_mode']=0o777
            if self.mode=='corrupt': probe['index_sha256']='0'*64
            return json.dumps(probe)
        if args[0]=='stop': self.running=False; return args[-1]
        if args[0]=='logs': return 'Application shutdown complete'
        if args[0]=='rm' or args[:2]==['network','rm']: return args[-1]
        raise AssertionError(args)
    def http(self,spec):
        last=json.loads((self.root/'reports'/run.RUN_ID/'actions.jsonl').read_text().splitlines()[-1])
        assert last['event']=='http_attempt' and last['request']==spec
        self.http_calls.append(spec)
        if self.mode=='http': return {'status':500,'body':'{}'}
        return {'status':spec['status'],'body':json.dumps({'detail':spec.get('detail','fixture'),'indexed':True})}

@pytest.fixture(autouse=True)
def no_actual_docker(monkeypatch):
    def forbidden(*args,**kwargs): raise AssertionError('No actual Docker runtime command is authorized in tests')
    monkeypatch.setattr(run.Native,'command',forbidden)
    monkeypatch.setattr(run.Native,'http',forbidden)

@pytest.fixture
def sandbox(tmp_path,monkeypatch):
    (tmp_path/'.local').mkdir(); (tmp_path/'reports').mkdir()
    for name in run.SOURCE_FILES:
        target=tmp_path/name; target.parent.mkdir(parents=True,exist_ok=True); shutil.copyfile(REAL_ROOT/name,target)
    history={'old.claim':b'consumed','historical.sqlite3':b'never opened as SQL','labels.json':b'frozen'}
    for n,v in history.items(): (tmp_path/n).write_bytes(v)
    snapshot={'files':{n:run.sha(tmp_path/n) for n in history},'optional_in_fresh_ci':[],'git_representations':{},'required_absent':['old-failed.sqlite3']}
    p=tmp_path/run.PREP/'preservation.json'; p.write_bytes(run.encoded(snapshot))
    monkeypatch.setattr(run,'ROOT',tmp_path); monkeypatch.setattr(run,'HISTORY_SHA',run.sha(p))
    actual_plan=run.plan()
    # Preserve the frozen context hashes using copied predecessor files.
    for name in actual_plan['build_context_files']:
        target=tmp_path/name; target.parent.mkdir(parents=True,exist_ok=True); shutil.copyfile(REAL_ROOT/name,target)
    def git(*args):
        if args[0]=='status': return b''
        if args[0]=='rev-parse': return b'd'*40
        if args[0]=='merge-base': return b''
        if args[0]=='diff': return b'evaluation/container_runtime.py\n'
        raise AssertionError(args)
    monkeypatch.setattr(run,'git',git)
    fake_paths={'workspace':tmp_path/'linux-fixture','database':tmp_path/'linux-fixture/data/support.sqlite3',
                'claim':tmp_path/'.local'/(run.RUN_ID+'.claim.json'),'output':tmp_path/'reports'/run.RUN_ID}
    monkeypatch.setattr(run,'paths',lambda:fake_paths)
    monkeypatch.setattr(run,'host_gate',lambda:{'os':'linux','uid':10001,'gid':10001,'filesystem':'ext4'})
    monkeypatch.setattr(run.time,'sleep',lambda _:None)
    (tmp_path/run.TEST_LOG).write_text('100 passed, 1 skipped in 1s\n',encoding='utf-8')
    run.record_offline_proof(0)
    from support_agent.production import volume
    def inspect(c): return {'sha256':run.sha(c.database),'dimension':768,'chunks':2}
    def backup(c,p):
        (p/'support.sqlite3').write_bytes(c.database.read_bytes()); return inspect(c)
    def restore(c,p,confirmation):
        assert confirmation=='REPLACE-STOPPED-INDEX'
        c.database.write_bytes((p/'support.sqlite3').read_bytes()); return inspect(c)
    monkeypatch.setattr(volume,'inspect',inspect); monkeypatch.setattr(volume,'backup',backup); monkeypatch.setattr(volume,'restore',restore)
    return tmp_path,history

def test_frozen_plan_history_and_actual_claim_output_absent():
    assert run.plan()['model_mode']=='disabled' and run.plan()['fake_uds_endpoint'] is False
    assert run.plan()['limits']['overall_seconds']==600 and run.plan()['limits']['docker_normal_actions']==17
    result=run.preservation()
    assert len(result['checked'])+len(result['skipped_optional'])==397
    p=run.paths()
    assert not p['claim'].exists() and not p['output'].exists()
    assert run.plan()['fixture_kind'].startswith('DETERMINISTIC_FAKE')

def test_complete_fake_runtime_and_persistence_backup_cleanup(sandbox):
    root,history=sandbox; docker=FakeDocker(root)
    provenance=run.preflight(docker); budget=run.Budget()
    result=run.execute(provenance,budget,transport=docker,hard_deadline=False)
    assert result['status']=='PASS',result
    assert result['normal_actions']==17 and result['cleanup_actions']==0 and len(docker.http_calls)==9
    assert result['backup_restore'].startswith('PASS') and result['actual_ollama_requests']==0
    assert all((root/n).read_bytes()==v for n,v in history.items())
    assert [c[0] for c in docker.calls].count('restart')==1
    assert not any(c[0] in {'pull','build','run','prune','system','model'} for c in docker.calls)
    out=root/'reports'/run.RUN_ID; manifest=json.loads((out/'artifacts.sha256.json').read_text())
    assert all(run.sha(out/n)==v for n,v in manifest.items())
    assert run.sha(root/'.local'/(run.RUN_ID+'.claim.json'))==run.sha(out/'claim.json')
    with pytest.raises(run.Blocked): run.unused()

@pytest.mark.parametrize('mode',['uid','mount','network','restart-loop','startup','shutdown','socket','permissions','corrupt','http','timeout','bad-id'])
def test_failure_retains_claim_journal_and_only_cleans_captured_ids(sandbox,mode):
    root,history=sandbox; docker=FakeDocker(root,mode)
    result=run.execute(run.preflight(docker),run.Budget(),transport=docker,hard_deadline=False)
    assert result['status']=='STOPPED' and result['preservation']=='PASS'
    assert result['cleanup_actions']<=6 and (root/'.local'/(run.RUN_ID+'.claim.json')).is_file()
    assert (root/'reports'/run.RUN_ID/'actions.jsonl').is_file()
    assert all((root/n).read_bytes()==v for n,v in history.items())
    assert len(docker.http_calls)<=9 and [c[0] for c in docker.calls].count('restart')<=1
    for c in docker.calls:
        if c[0] in {'stop','rm'}: assert c[-1] in IDS.values()
        assert not any(a in {'--force','-f','--all','system','prune'} for a in c)
    with pytest.raises(run.Blocked): run.execute({},run.Budget(),transport=docker,hard_deadline=False)

@pytest.mark.parametrize('field,value',[('DefaultRuntime','sysbox-runc'),('SecurityOptions',['name=userns']),('Architecture','aarch64'),
    ('MemTotal',1000),('CpuCfsQuota',False),('MemoryLimit',False),('ID','different-engine')])
def test_unsupported_engine_refuses_before_claim(sandbox,field,value):
    root,_=sandbox; docker=FakeDocker(root); original=docker.info
    docker.info=lambda:{**original(),field:value}
    with pytest.raises(run.Blocked): run.preflight(docker)
    assert not run.paths()['claim'].exists() and not run.paths()['workspace'].exists()

def test_incompatible_images_and_stale_proof_refuse(sandbox):
    root,_=sandbox; docker=FakeDocker(root); original=docker.image_info
    def wrong():
        value=original(); value[0]['Config']['Labels']['org.project5.context']='0'*64; return value
    docker.image_info=wrong
    with pytest.raises(run.Blocked): run.preflight(docker)
    docker.image_info=original
    proof=root/run.PROOF; data=json.loads(proof.read_text()); data['commit']='wrong'; proof.write_bytes(run.encoded(data))
    with pytest.raises(run.Blocked,match='offline_proof'): run.preflight(docker)

def test_budget_exact_sequence_http_no_retries_and_deadline():
    budget=run.Budget()
    with pytest.raises(run.StopRun): budget.reserve('backend_restart')
    for action in run.plan()['actions']: budget.reserve(action)
    with pytest.raises(run.StopRun): budget.reserve('network_create')
    for spec in run.plan()['http']: budget.reserve_http(spec)
    with pytest.raises(run.StopRun): budget.reserve_http(run.plan()['http'][0])
    values=iter([0,541,599,601]); budget=run.Budget(clock=lambda:next(values))
    with pytest.raises(run.StopRun): budget.remaining()
    assert budget.remaining(True)==1
    with pytest.raises(run.StopRun): budget.remaining(True)

def test_model_or_network_override_arguments_do_not_exist(sandbox):
    for args in ([],['--run-id','new'],['--workspace','/tmp/other'],['--host-network'],['--model','nomic'],['--resume']):
        with pytest.raises(SystemExit): run.main(args)
    with pytest.raises(run.StopRun,match='fake_docker'): run.execute({},run.Budget())

def test_read_only_preparation_mode_does_not_consume_claim(sandbox,monkeypatch,capsys):
    root,_=sandbox; monkeypatch.setattr(run,'preflight',lambda:(_ for _ in ()).throw(run.Blocked('unsupported_uid_mapping')))
    assert run.main(['--check-preparation'])==2
    assert json.loads(capsys.readouterr().out)['status']=='BLOCKED'
    assert not run.paths()['claim'].exists() and not run.paths()['output'].exists()

def test_cleanup_refuses_other_owners_and_does_not_retry_failed_stops(sandbox):
    root,_=sandbox; docker=FakeDocker(root); original=docker.command; failed=[False]
    def command(args,seconds):
        if args[0]=='stop' and not failed[0]: failed[0]=True; docker.calls.append(args); raise run.StopRun('uncertain_stop')
        return original(args,seconds)
    docker.command=command
    result=run.execute(run.preflight(docker),run.Budget(),transport=docker,hard_deadline=False)
    assert result['status']=='STOPPED'
    assert sum(c[0]=='stop' and c[-1]==IDS['backend'] for c in docker.calls)==1

def test_fixture_is_new_and_explicitly_synthetic(sandbox):
    root,_=sandbox; f=run.fixture(root/'fixture-only')
    assert 'NOT_NOMIC_INFERENCE' in f['kind'] and Path(f['database']).is_file()
    with pytest.raises(FileExistsError): run.fixture(root/'fixture-only')

@pytest.mark.parametrize('which',['claim','output','workspace'])
def test_any_existing_fixed_resource_refuses_before_new_claim(sandbox,which):
    root,_=sandbox; path=run.paths()[which]
    if which=='claim': path.write_text('consumed')
    else: path.mkdir()
    with pytest.raises(run.Blocked): run.unused()
    if which!='claim': assert not run.paths()['claim'].exists()

def test_host_sqlite_guard_rejects_historical_indexes(sandbox):
    import sqlite3
    root,_=sandbox; workspace=root/'owned'; workspace.mkdir(); (workspace/'data').mkdir()
    journal=run.Journal(root/'guard.jsonl')
    with run.isolated_sql(workspace,journal):
        with pytest.raises(run.StopRun): sqlite3.connect(root/'historical.sqlite3')
        with pytest.raises(run.StopRun): sqlite3.connect((workspace/'data/support.sqlite3').as_uri()+'?mode=rw',uri=True)
        with sqlite3.connect(workspace/'data/support.sqlite3') as db: db.execute('CREATE TABLE invented(value)')

def test_foreign_label_prevents_cleanup_of_unknown_ownership(sandbox):
    root,_=sandbox; docker=FakeDocker(root,'http'); original=docker.containers
    def containers():
        values=original(); values[1]['Config']['Labels']['org.project5.run']='other-project'; return values
    docker.containers=containers
    result=run.execute(run.preflight(docker),run.Budget(),transport=docker,hard_deadline=False)
    assert result['status']=='STOPPED' and result['cleanup'].startswith('incomplete')
    assert not any(c[0] in {'stop','rm'} for c in docker.calls)

def test_native_command_is_shell_free_output_bounded_and_uses_local_context(monkeypatch):
    from types import SimpleNamespace
    reads=[b'bounded fixture',b'']; processes=[]
    class Pipe:
        def fileno(self): return 91919
        def close(self): pass
    class Process:
        stdout=Pipe(); returncode=None
        def wait(self,timeout): self.returncode=0; return 0
        def poll(self): return self.returncode
        def kill(self): self.returncode=-9
    def popen(argv,**kwargs):
        assert argv==['docker','--context','default','info'] and not kwargs.get('shell',False)
        value=Process(); processes.append(value); return value
    class Selector:
        active=True
        def register(self,*a): pass
        def get_map(self): return {1:1} if self.active else {}
        def select(self,seconds): return [(SimpleNamespace(fileobj=Pipe()),None)]
        def unregister(self,*a): self.active=False
        def close(self): pass
    original_read=os.read
    monkeypatch.setattr(run.subprocess,'Popen',popen)
    monkeypatch.setattr(run.selectors,'DefaultSelector',Selector)
    monkeypatch.setattr(run.os,'read',lambda fd,n:reads.pop(0) if fd==91919 else original_read(fd,n))
    assert NATIVE_COMMAND(run.Native(),['info'],1)=='bounded fixture'
    reads[:]=[b'x'*1_000_001]
    with pytest.raises(run.StopRun,match='output_limit'): NATIVE_COMMAND(run.Native(),['info'],1)
    assert processes[-1].returncode is not None

def test_native_http_never_redirects_or_uses_model_port(monkeypatch):
    calls=[]
    class Reply:
        status=503
        def read(self,limit): assert limit==65537; return b'{"detail":"model_disabled"}'
    class Connection:
        def __init__(self,host,port,timeout): assert (host,port,timeout)==('127.0.0.1',5173,3)
        def request(self,*args): calls.append(args)
        def getresponse(self): return Reply()
        def close(self): pass
    monkeypatch.setattr(run.http.client,'HTTPConnection',Connection)
    spec=run.plan()['http'][4]
    assert NATIVE_HTTP(run.Native(),spec)['status']==503
    assert calls[0][0:2]==('POST','/api/ask')

def test_linux_host_gate_rejects_drvfs_and_uid_remapping_without_container_probe(monkeypatch):
    from types import SimpleNamespace
    import support_agent.documents
    class Parent:
        def stat(self): return SimpleNamespace(st_uid=0,st_mode=0o1777)
        def __str__(self): return '/var/tmp'
    class Text:
        def __init__(self,text): self.text=text
        def read_text(self): return self.text
    filesystem=['ext4']; original=run.Path
    def path(value):
        if value=='/var/tmp': return Parent()
        if value=='/proc/self/mountinfo': return Text('1 0 8:1 / / rw - '+filesystem[0]+' /dev/test rw')
        if value=='/proc/meminfo': return Text('MemAvailable: 4000000 kB')
        return original(value)
    monkeypatch.setattr(run.sys,'platform','linux'); monkeypatch.setattr(run,'Path',path)
    monkeypatch.setattr(support_agent.documents,'local_path',lambda p:p)
    monkeypatch.setattr(run.os,'getuid',lambda:10001,raising=False); monkeypatch.setattr(run.os,'getgid',lambda:10001,raising=False)
    for key in ('DOCKER_HOST','DOCKER_CONTEXT','DOCKER_CONFIG','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH','DOCKER_API_VERSION'): monkeypatch.delenv(key,raising=False)
    assert run.host_gate()['filesystem']=='ext4'
    filesystem[0]='9p'
    with pytest.raises(run.Blocked,match='ext4'): run.host_gate()
    filesystem[0]='ext4'; monkeypatch.setenv('DOCKER_HOST','https://external.invalid')
    with pytest.raises(run.Blocked,match='override'): run.host_gate()

def test_image_environment_and_host_namespace_configuration_are_not_accepted(sandbox):
    root,_=sandbox; docker=FakeDocker(root); images=docker.image_info()
    images[0]['Config']['Env'].append('SECRET=must-not-be-written')
    docker.image_info=lambda:images
    with pytest.raises(run.Blocked,match='environment'): run.preflight(docker)
    assert not run.paths()['claim'].exists()

def test_runtime_caps_readonly_memory_and_model_flags_are_individually_checked():
    docker=FakeDocker(); docker.running=True
    changes=[('ReadonlyRootfs',False),('Privileged',True),('CapDrop',[]),('PidsLimit',1000),('Memory',0),('SecurityOpt',['seccomp=unconfined']),('ExtraHosts',['host.docker.internal:host-gateway'])]
    for key,value in changes:
        items=docker.containers(); items[1]['HostConfig'][key]=value
        with pytest.raises(run.StopRun): run.verify_containers(items,IDS,NETWORK,IMAGES)
    items=docker.containers(); items[1]['Config']['Env']=['RAG_MODEL_MODE=uds']
    with pytest.raises(run.StopRun): run.verify_containers(items,IDS,NETWORK,IMAGES)


def test_pinned_image_environments_accept_order_independent_exact_values(sandbox):
    root,_=sandbox; docker=FakeDocker(root); original=docker.image_info
    def reversed_env():
        images=original()
        for image in images: image['Config']['Env'].reverse()
        return images
    docker.image_info=reversed_env
    assert run.preflight(docker)['engine']['images']==IMAGES
    assert not run.paths()['claim'].exists()


@pytest.mark.parametrize('role,operation,entry',[
    ('backend','replace','LANG=en_US.UTF-8'),
    ('backend','replace','GPG_KEY=0000000000000000000000000000000000000000'),
    ('backend','append','LANG=C.UTF-8'),
    ('backend','append','GPG_KEY=7169605F62C751356D054A26A821E680E5FA6305'),
    ('backend','append','LANG=untrusted'),
    ('backend','append','UNKNOWN=untrusted'),
    ('backend','append','PYTHONSTARTUP=/tmp/start.py'),
    ('backend','append','PYTHON_API_KEY=fictional'),
    ('backend','replace','PATH=/tmp:/usr/bin'),
    ('backend','replace','PYTHONPATH=/tmp'),
    ('backend','append','HTTP_PROXY=http://external.invalid'),
    ('backend','append','http_proxy=http://external.invalid'),
    ('backend','append','HTTPS_PROXY='),
    ('backend','append','ALL_PROXY=socks5://external.invalid'),
    ('backend','append','OLLAMA_HOST=http://127.0.0.1:11434'),
    ('backend','append','RAG_MODEL_MODE=uds'),
    ('backend','append','AWS_ACCESS_KEY_ID=fictional'),
    ('backend','append','GOOGLE_APPLICATION_CREDENTIALS=/tmp/fictional.json'),
    ('backend','append','LD_PRELOAD=/tmp/untrusted.so'),
    ('backend','append','LD_LIBRARY_PATH=/tmp'),
    ('backend','remove','LANG=C.UTF-8'),
    ('backend','append','BROKEN_ENTRY'),
    ('backend','append',None),
    ('frontend','append','LANG=C.UTF-8'),
    ('frontend','append','GPG_KEY=7169605F62C751356D054A26A821E680E5FA6305'),
    ('frontend','append','NGINX_UNKNOWN=untrusted'),
    ('frontend','append','NJS_VERSION=1.0.2'),
    ('frontend','replace','PATH=/tmp:/usr/bin'),
    ('frontend','append','OLLAMA_HOST=http://127.0.0.1:11434'),
    ('frontend','append','HTTPS_PROXY=http://external.invalid'),
])
def test_image_environment_manipulation_refuses_without_claim(sandbox,role,operation,entry):
    root,_=sandbox; docker=FakeDocker(root); images=docker.image_info()
    env=next(i['Config']['Env'] for i in images if i['Config']['User']==('10001:10001' if role=='backend' else '101:101'))
    if operation=='replace':
        key=entry.split('=',1)[0]
        env[:]=[entry if e.split('=',1)[0]==key else e for e in env]
    elif operation=='remove': env.remove(entry)
    else: env.append(entry)
    docker.image_info=lambda:images
    with pytest.raises(run.Blocked,match='unexpected_image_environment'): run.preflight(docker)
    assert not run.paths()['claim'].exists() and not run.paths()['output'].exists()


@pytest.mark.parametrize('entries',[None,{},'PATH=/bin',42])
def test_image_environment_requires_a_structured_list(entries):
    with pytest.raises(run.Blocked,match='unexpected_image_environment'):
        run.validate_image_environment(entries,'backend')
