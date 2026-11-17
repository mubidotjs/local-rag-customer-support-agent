"""Fixed model-disabled Linux runtime acceptance. Preparation is NOT execution.

Only --execute-authorized-once after separate owner approval may mutate Docker.
No pull/build, context switching, host networking, external model or path flags.
"""
import argparse
from contextlib import contextmanager
from dataclasses import replace
import hashlib
import http.client
import json
import os
from pathlib import Path
import selectors
import socket
import stat
import sqlite3
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit
from urllib.request import url2pathname
from unittest.mock import patch
from evaluation.lifecycle_live import Journal,StopRun,save_new,sha,encoded,utc

ROOT=Path(__file__).resolve().parents[1]
BASELINE='f267a6dbfd3a67aae1e8285e8cca3e05897b1539'
RUN_ID='container-runtime-5b-once'
PREP=Path('reports/runtime-preparation')
PLAN_SHA='3be4f849bff92b4e836328f4082b8a531259349814a3b145fac870602296c0e3'
HISTORY_SHA='77fd1dccf602fc6669ef0d63965b03f7754357fac5e435d60041b48989d7e6df'
PROOF=Path('.local/runtime-5b-offline-proof.json')
TEST_LOG=Path('.local/runtime-5b-offline-tests.log')
SOURCE_FILES=('evaluation/container_runtime.py','tests/test_container_runtime.py','.github/workflows/runtime-preparation.yml',
              (PREP/'plan.json').as_posix(),(PREP/'preservation.json').as_posix())
ALLOWED=set(SOURCE_FILES)|{'docs/runtime-preparation.md',*[(PREP/n).as_posix() for n in ('assessment.json','report.md','offline.json','coverage.json')]}

class Blocked(StopRun): pass

def frozen(name,expected):
    path=ROOT/PREP/name
    if sha(path)!=expected: raise Blocked('frozen_input_changed')
    return json.loads(path.read_text(encoding='utf-8'))

def plan(): return frozen('plan.json',PLAN_SHA)

def preservation(require_local=False):
    value=frozen('preservation.json',HISTORY_SHA); checked={}; skipped=[]
    for name,expected in value['files'].items():
        path=ROOT/name
        if not path.exists() and not require_local and name in value['optional_in_fresh_ci']:
            skipped.append(name); continue
        actual=sha(path)
        if actual not in {expected,value['git_representations'].get(name,expected)}: raise Blocked('historical_file_changed:'+name)
        checked[name]=actual
    if any((ROOT/n).exists() or (ROOT/n).is_symlink() for n in value['required_absent']): raise Blocked('historical_absence_changed')
    return {'checked':checked,'skipped_optional':skipped}

def git(*args):
    return subprocess.check_output(['git','-c','safe.directory='+ROOT.as_posix(),*args],cwd=ROOT,timeout=10)

def sources(): return {n:sha(ROOT/n) for n in SOURCE_FILES}

def record_offline_proof(exit_code):
    log=(ROOT/TEST_LOG).read_text(encoding='utf-8-sig')
    import re
    if exit_code or not re.search(r'(?m)^\d+ passed(?:,| in )',log) or re.search(r'(?m)^.*\d+ failed',log): raise Blocked('successful_offline_suite_required')
    value={'commit':git('rev-parse','HEAD').decode().strip(),'source_sha256':sources(),'log_sha256':sha(ROOT/TEST_LOG),'exit_code':0,'recorded_at_utc':utc()}
    temporary=ROOT/PROOF.with_suffix('.tmp')
    with temporary.open('wb') as stream:
        stream.write(encoded(value)); stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary,ROOT/PROOF)
    return value

def paths():
    value=plan()
    expected={'workspace':'/var/tmp/project5-runtime-5b-once','database':'/var/tmp/project5-runtime-5b-once/data/support.sqlite3',
              'claim':'.local/'+RUN_ID+'.claim.json','output':'reports/'+RUN_ID}
    if value['run_id']!=RUN_ID or any(value[k]!=v for k,v in expected.items()): raise Blocked('fixed_path_mismatch')
    return {'workspace':Path(expected['workspace']),'database':Path(expected['database']),
            'claim':ROOT/expected['claim'],'output':ROOT/expected['output']}

def unused():
    value=paths()
    for k in ('claim','output'):
        if value[k].exists() or value[k].is_symlink(): raise Blocked('claim_or_output_already_exists')
    if value['workspace'].exists() or value['workspace'].is_symlink(): raise Blocked('workspace_already_exists')
    return value

def host_gate():
    if not sys.platform.startswith('linux'): raise Blocked('linux_orchestrator_required_windows_not_attested')
    if os.getuid()!=10001 or os.getgid()!=10001: raise Blocked('uid_gid_10001_required')
    from support_agent.documents import local_path
    local_path(Path('/var/tmp'))
    parent=Path('/var/tmp').stat()
    if parent.st_uid!=0 or not parent.st_mode & stat.S_ISVTX: raise Blocked('trusted_sticky_parent_required')
    mounts=Path('/proc/self/mountinfo').read_text()[:1_000_000].splitlines()
    candidates=[]
    for line in mounts:
        left,_,right=line.partition(' - '); fields=left.split(); kind=right.split()
        if len(fields)>4 and kind and (str(Path('/var/tmp'))==fields[4] or str(Path('/var/tmp')).startswith(fields[4].rstrip('/')+'/')):
            candidates.append((len(fields[4]),kind[0]))
    if not candidates or max(candidates)[1]!='ext4': raise Blocked('linux_ext4_workspace_required')
    memory={line.split(':')[0]:line.split(':')[1].strip().split()[0] for line in Path('/proc/meminfo').read_text().splitlines() if ':' in line}
    if int(memory.get('MemAvailable','0'))*1024<1_500_000_000: raise Blocked('insufficient_available_memory')
    if any(os.environ.get(k) for k in ('DOCKER_HOST','DOCKER_CONTEXT','DOCKER_CONFIG','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH','DOCKER_API_VERSION')):
        raise Blocked('docker_environment_override_forbidden')
    return {'os':'linux','uid':10001,'gid':10001,'filesystem':'ext4'}

class Native:
    def command(self,args,seconds):
        # Linux pipe reads are bounded before buffering, without shell execution.
        process=subprocess.Popen(['docker','--context','default',*args],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL)
        selector=selectors.DefaultSelector(); selector.register(process.stdout,selectors.EVENT_READ)
        deadline=time.monotonic()+seconds; data=bytearray()
        try:
            while selector.get_map():
                left=deadline-time.monotonic()
                if left<=0: raise StopRun('docker_command_timeout')
                for key,_ in selector.select(min(left,0.25)):
                    block=os.read(key.fileobj.fileno(),65536)
                    if not block: selector.unregister(key.fileobj); continue
                    data.extend(block)
                    if len(data)>1_000_000: raise StopRun('docker_output_limit')
            code=process.wait(timeout=max(.01,deadline-time.monotonic()))
            if code:
                failure=StopRun('docker_command_failed'); failure.details=data.decode('utf-8',errors='replace'); raise failure
            return data.decode('utf-8')
        finally:
            selector.close(); process.stdout.close()
            if process.poll() is None: process.kill(); process.wait(timeout=2)

    def http(self,spec):
        headers={'Host':spec.get('host','127.0.0.1:5173'),'Origin':spec.get('origin','http://127.0.0.1:5173')}
        body=json.dumps({'question':spec['question']}) if 'question' in spec else None
        if body: headers['Content-Type']='application/json'
        connection=http.client.HTTPConnection('127.0.0.1',5173,timeout=3)
        try:
            connection.request(spec['method'],spec['path'],body,headers)
            response=connection.getresponse(); raw=response.read(65537)
            if len(raw)>65536: raise StopRun('http_output_limit')
            return {'status':response.status,'body':raw.decode('utf-8')}
        finally: connection.close()

def docker_gate(transport):
    value=plan(); commands=[['context','show'],['context','inspect','default'],['version','--format','{{json .}}'],
        ['info','--format','{{json .}}'],['image','inspect',*value['image_tags'].values()],
        ['ps','-a','--filter','name=^'+value['docker_project'],'--format','{{json .}}'],
        ['network','ls','--filter','name=^'+value['docker_project']+'-isolated$','--format','{{json .}}']]
    results=[transport.command(c,5) for c in commands]
    if results[0].strip()!='default': raise Blocked('selected_context_mismatch')
    endpoint=json.loads(results[1])[0]['Endpoints']['docker']['Host']
    server=json.loads(results[2]).get('Server'); info=json.loads(results[3])
    if endpoint!=value['docker_endpoint'] or not server or server['Os']!='linux' or server['Arch']!='amd64': raise Blocked('unsupported_engine')
    if (info['ID']!=value['engine_id'] or info['DefaultRuntime']!='runc' or info['Architecture'] not in {'x86_64','amd64'}
        or any('rootless' in s or 'userns' in s for s in info['SecurityOptions']) or info['MemTotal']<2_000_000_000
        or info['CgroupVersion']!='2' or not info['MemoryLimit'] or not info['PidsLimit'] or not info['CpuCfsQuota'] or not info['CpuCfsPeriod'] or info['NCPU']<2): raise Blocked('engine_identity_or_isolation_mismatch')
    if results[5].strip() or results[6].strip(): raise Blocked('fixed_docker_resources_already_exist')
    images={}
    for image in json.loads(results[4]):
        config=image['Config']; labels=config.get('Labels') or {}
        if (labels.get(value['image_source_label'])!=BASELINE or labels.get(value['image_context_label'])!=value['build_context_sha256']
            or image['Os']!='linux' or image['Architecture']!='amd64' or config.get('Volumes') or not config.get('Healthcheck')):
            raise Blocked('image_source_or_configuration_mismatch')
        user=config['User']; role='backend' if user=='10001:10001' else 'frontend' if user=='101:101' else None
        if role is None or role in images or not image['Id'].startswith('sha256:') or len(image['Id'])!=71 or any(c not in '0123456789abcdef' for c in image['Id'][7:]): raise Blocked('image_identity_mismatch')
        allowed=('PATH=','PYTHON','NGINX_','PKG_RELEASE=','DYNPKG_RELEASE=','NJS_')
        if any(not e.startswith(allowed) for e in config.get('Env',[])): raise Blocked('unexpected_image_environment')
        expected=['python','-m','support_agent.production'] if role=='backend' else ['nginx','-g','daemon off;']
        if config['Entrypoint']!=expected: raise Blocked('image_entrypoint_mismatch')
        images[role]=image['Id']
    if set(images)!={'backend','frontend'}: raise Blocked('two_preloaded_images_required')
    return {'engine_id':info['ID'],'context':'default','images':images,'metadata_commands':len(commands)}

def preflight(transport=None):
    if git('status','--porcelain').strip(): raise Blocked('clean_tree_required')
    head=git('rev-parse','HEAD').decode().strip(); git('merge-base','--is-ancestor',BASELINE,head)
    if set(git('diff','--name-only',BASELINE,head).decode().splitlines())-ALLOWED: raise Blocked('source_allowlist_mismatch')
    value=plan(); history=preservation(True); unused()
    for name,expected in value['build_context_files'].items():
        if sha(ROOT/name)!=expected: raise Blocked('image_build_context_changed')
    proof=json.loads((ROOT/PROOF).read_text())
    if proof.get('commit')!=head or proof.get('source_sha256')!=sources() or proof.get('log_sha256')!=sha(ROOT/TEST_LOG): raise Blocked('offline_proof_stale')
    host=host_gate()
    try: engine=docker_gate(transport or Native())
    except Blocked: raise
    except (StopRun,KeyError,TypeError,ValueError,subprocess.SubprocessError) as exc: raise Blocked('docker_preflight_incomplete') from exc
    return {'run_id':RUN_ID,'commit':head,'host':host,'engine':engine,'history':history['checked'],'plan_sha256':PLAN_SHA,'offline_proof':proof}

PROBE="""import os,json,stat,ctypes,hashlib,sqlite3,sys
from pathlib import Path
for name,expected in json.loads(sys.argv[1]).items():
 assert name.startswith('support_agent/') and hashlib.sha256((Path('/app')/name).read_bytes()).hexdigest()==expected
from support_agent.store import connect
from support_agent.production.config import Config
from support_agent.production.storage import Guard
c=Config.environment(); guard=Guard(c); ready,reason=guard.check()
assert c.mode=='disabled' and not ready and reason=='model_disabled'
p=c.database; s=p.stat(); d=c.data_dir.stat()
lib=ctypes.CDLL(None,use_errno=True); buf=ctypes.create_string_buffer(256)
assert lib.statfs(os.fsencode(c.data_dir),ctypes.byref(buf))==0
assert ctypes.c_long.from_buffer(buf).value==0xef53
with connect(p) as db:
 assert db.execute('PRAGMA query_only').fetchone()[0]==1
 try: db.execute('DELETE FROM documents')
 except sqlite3.OperationalError: pass
 else: raise AssertionError('SQL mutation accepted')
for path in (p,Path('/app/support_agent/api.py')):
 try: fd=os.open(path,os.O_WRONLY)
 except OSError: pass
 else: os.close(fd); raise AssertionError('Filesystem writable')
assert os.statvfs(p).f_flag & os.ST_RDONLY and os.statvfs('/app').f_flag & os.ST_RDONLY
assert Path('/proc/self/uid_map').read_text().split()==['0','0','4294967295']
assert Path('/proc/self/gid_map').read_text().split()==['0','0','4294967295']
assert int(Path('/sys/fs/cgroup/memory.max').read_text())==805306368
assert int(Path('/sys/fs/cgroup/pids.max').read_text())==64
quota,period=map(int,Path('/sys/fs/cgroup/cpu.max').read_text().split()); assert quota==period
print(json.dumps({'uid':os.getuid(),'gid':os.getgid(),'data_uid':d.st_uid,'data_gid':d.st_gid,'data_mode':stat.S_IMODE(d.st_mode),'mode':c.mode,'index_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'data_identity':[d.st_dev,d.st_ino],'database_identity':[s.st_dev,s.st_ino],'model_socket_exists':c.socket.exists(),'mutation_rejected':True,'root_readonly':True,'data_readonly':True}))
"""

class Budget:
    def __init__(self,clock=time.monotonic): self.clock=clock; self.started=clock(); self.normal=0; self.cleanup=0; self.http_count=0; self.failed=False
    def remaining(self,cleanup=False):
        left=(600 if cleanup else 540)-(self.clock()-self.started)
        if left<=0: raise StopRun('overall_or_work_timeout')
        return left
    def reserve(self,name):
        self.remaining()
        actions=plan()['actions']
        if self.failed or self.normal>=len(actions) or name!=actions[self.normal]: raise StopRun('unexpected_or_repeated_action')
        self.normal+=1
    def reserve_http(self,spec):
        self.remaining()
        expected=plan()['http']
        if self.failed or self.http_count>=len(expected) or spec!=expected[self.http_count]: raise StopRun('unexpected_http_or_retry')
        self.http_count+=1

@contextmanager
def isolated_sql(workspace,journal):
    original=sqlite3.connect
    allowed={(workspace/'data/support.sqlite3').resolve(),(workspace/'backup/support.sqlite3').resolve()}
    def connect(value,*args,**kwargs):
        if str(value).startswith('file:'):
            uri=urlsplit(str(value))
            if kwargs.get('uri') is not True or uri.netloc or uri.query!='mode=ro' or uri.fragment: raise StopRun('unexpected_sqlite_uri')
            target=Path(url2pathname(uri.path))
        else: target=Path(value)
        if target.resolve() not in allowed: raise StopRun('historical_or_external_database_access')
        journal.write({'event':'host_sqlite_open','database':str(target.resolve())})
        return original(value,*args,**kwargs)
    with patch('sqlite3.connect',connect): yield

def create_args(role,images,network,frontend):
    value=plan(); project=value['docker_project']; backend=role=='backend'
    args=['create','--pull=never','--runtime=runc','--name',project+'-'+role,'--label','org.project5.run='+RUN_ID,
        '--user','10001:10001' if backend else '101:101','--read-only','--cap-drop=ALL','--security-opt=no-new-privileges:true',
        '--init','--restart=on-failure:3','--stop-timeout=20','--cpus','1' if backend else '.25','--memory','768m' if backend else '128m',
        '--memory-reservation','256m' if backend else '32m','--pids-limit','64' if backend else '32',
        '--tmpfs','/tmp:rw,noexec,nosuid,nodev,size=16m,uid='+('10001,gid=10001' if backend else '101,gid=101')+',mode=0700',
        '--log-driver=local','--log-opt=max-size=1m','--log-opt=max-file=3']
    for key in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy'): args+=['--env',key+'=']
    args+=['--env','NO_PROXY=127.0.0.1,localhost','--env','no_proxy=127.0.0.1,localhost']
    if backend: args+=['--network','container:'+frontend,'--env','RAG_MODEL_MODE=disabled','--mount','type=bind,source='+value['workspace']+'/data,target=/var/lib/rag,readonly,bind-propagation=rprivate']
    else: args+=['--network',network,'--publish','127.0.0.1:5173:8080']
    return args+[images[role]]

def verify_containers(items,ids,network,images):
    if len(items)!=2: raise StopRun('service_count_mismatch')
    for role,cid in ids.items():
        item=next((i for i in items if i['Id']==cid),None)
        if item is None: raise StopRun('container_id_mismatch')
        host=item['HostConfig']; config=item['Config']; backend=role=='backend'
        if (item['Image']!=images[role] or config['Labels'].get('org.project5.run')!=RUN_ID or config['User']!=('10001:10001' if backend else '101:101')
            or not host['ReadonlyRootfs'] or host['Privileged'] or host.get('CapAdd') or host['CapDrop']!=['ALL']
            or set(host['SecurityOpt']) not in ({'no-new-privileges:true'},{'no-new-privileges'}) or not host['Init']
            or host['NanoCpus']!=(1_000_000_000 if backend else 250_000_000) or host['Memory']!=(805306368 if backend else 134217728)
            or host['PidsLimit']!=(64 if backend else 32) or host['RestartPolicy']!={'Name':'on-failure','MaximumRetryCount':3}
            or host['MemoryReservation']!=(268435456 if backend else 33554432)
            or item['RestartCount']!=0 or not item['State']['Running'] or host.get('Devices') or host.get('DeviceRequests')
            or host.get('ExtraHosts') or host.get('PidMode') or host.get('UsernsMode')): raise StopRun('runtime_security_mismatch')
        if backend:
            if host['NetworkMode']!='container:'+ids['frontend'] or host.get('PortBindings'): raise StopRun('backend_network_mismatch')
            mounts=item['Mounts']
            if len(mounts)!=1 or mounts[0]['Type']!='bind' or mounts[0]['Destination']!='/var/lib/rag' or mounts[0]['RW']:
                raise StopRun('data_mount_mismatch')
            if 'RAG_MODEL_MODE=disabled' not in config['Env'] or any('OLLAMA' in e for e in config['Env']): raise StopRun('model_route_forbidden')
        else:
            networks=item['NetworkSettings']['Networks']
            if (host['PortBindings']!={'8080/tcp':[{'HostIp':'127.0.0.1','HostPort':'5173'}]} or item['Mounts'] or len(networks)!=1
                or next(iter(networks.values()))['NetworkID']!=network or host['NetworkMode'] not in {network,plan()['docker_project']+'-isolated'}):
                raise StopRun('frontend_network_or_mount_mismatch')

def fixture(workspace):
    from support_agent.config import Settings
    from support_agent.embeddings import Binding,PINNED_MODEL,PINNED_DIGEST,config_digest
    from support_agent.store import ingest,connect
    value=plan(); workspace.mkdir(mode=0o700); data=workspace/'data'; data.mkdir(mode=0o700)
    sources_dir=workspace/'sources'; sources_dir.mkdir(mode=0o700)
    for d in value['corpus']: (sources_dir/(d['id']+'.md')).write_text(d['source'],encoding='utf-8',newline='\n')
    settings=replace(Settings(),db_path=data/'support.sqlite3',managed_embeddings=True)
    class Synthetic768:
        binding=Binding(PINNED_MODEL,PINNED_DIGEST,config_digest(settings))
        def embed(self,texts): return [[1.0]+[0.0]*767 for _ in texts]
    ingest(sources_dir,settings,Synthetic768()); os.chmod(settings.db_path,0o600)
    s=settings.db_path.stat(); d=data.stat()
    return {'kind':value['fixture_kind'],'index_sha256':sha(settings.db_path),'data_identity':[d.st_dev,d.st_ino],
            'database_identity':[s.st_dev,s.st_ino],'database':str(settings.db_path),'data_uid':d.st_uid,'data_gid':d.st_gid,'data_mode':stat.S_IMODE(d.st_mode)}

def verify_probe(value,expected):
    if (value.get('uid')!=10001 or value.get('gid')!=10001 or value.get('data_uid')!=10001 or value.get('data_gid')!=10001
        or value.get('data_mode')!=0o700 or value.get('mode')!='disabled' or value.get('model_socket_exists') is not False
        or not value.get('mutation_rejected') or not value.get('root_readonly') or not value.get('data_readonly')
        or any(value.get(k)!=expected[k] for k in ('index_sha256','data_identity','database_identity'))): raise StopRun('filesystem_or_uid_attestation_failed')

def execute(provenance,budget,*,transport=None,hard_deadline=True):
    if os.environ.get('PYTEST_CURRENT_TEST') and transport is None: raise StopRun('offline_tests_require_fake_docker')
    if transport is None and not hard_deadline: raise StopRun('hard_deadline_required')
    value=plan(); target=unused(); history=preservation(transport is None)
    if provenance.get('run_id')!=RUN_ID or provenance.get('plan_sha256')!=PLAN_SHA or provenance.get('history')!=history['checked']:
        raise StopRun('execution_provenance_mismatch')
    if (provenance.get('commit')!=git('rev-parse','HEAD').decode().strip() or git('status','--porcelain').strip()
        or provenance.get('offline_proof',{}).get('source_sha256')!=sources()): raise StopRun('execution_source_changed')
    if transport is None: host_gate()
    transport=transport or Native()
    save_new(target['claim'],{'run_id':RUN_ID,'consumed':True,'commit':provenance['commit'],'plan_sha256':PLAN_SHA,
                             'images':provenance['engine']['images'],'rule':'Never remove, resume or reuse this claim','utc':utc()})
    target['output'].mkdir(); output=target['output']; journal=Journal(output/'actions.jsonl')
    save_new(output/'claim.json',json.loads(target['claim'].read_text())); save_new(output/'preflight.json',provenance)
    report={'status':'INCOMPLETE_NOT_PASS','run_id':RUN_ID,'execution_sha':provenance['commit'],'plan_sha256':PLAN_SHA,
            'model_mode':'disabled','actual_ollama_requests':0,'downloads':0,'retries':0,'actions':[],'http':[]}
    ids={}; network=None; removed=set(); stopped=set(); stop_attempted=set(); remove_attempted=set(); network_remove_attempted=False; first_started=None
    def checkpoint():
        report.update(seconds=round(budget.clock()-budget.started,6),created_ids=dict(ids),network_id=network,normal_actions=budget.normal,cleanup_actions=budget.cleanup)
        temporary=output/'checkpoint.tmp'
        with temporary.open('wb') as stream: stream.write(encoded(report)); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary,output/'checkpoint.json')
    checkpoint()
    def command(name,args,cleanup=False):
        if cleanup:
            if budget.cleanup>=6: raise StopRun('cleanup_budget')
            budget.remaining(True); budget.cleanup+=1
        else: budget.reserve(name)
        number=len(report['actions'])+1
        journal.write({'event':'action_attempt','number':number,'name':name,'argv':args,'cleanup':cleanup})
        record={'name':name,'argv':args,'cleanup':cleanup,'outcome':'failed'}; report['actions'].append(record)
        try:
            raw=transport.command(args,min(20,budget.remaining(cleanup)))
            if len(raw.encode())>1_000_000: raise StopRun('docker_output_limit')
            save_new(output/f'action-{number:02d}.json',{'name':name,'stdout':raw})
            record['outcome']='ok'; return raw
        except BaseException as exc:
            record['error_type']=type(exc).__name__
            if hasattr(exc,'details'): save_new(output/f'action-{number:02d}-failure.json',{'name':name,'stdout':exc.details[:1_000_000]})
            raise
        finally: journal.write({'event':'action_finished','number':number,**record}); checkpoint()
    def http_range(start,end):
        for spec in value['http'][start:end]:
            budget.reserve_http(spec); journal.write({'event':'http_attempt','number':budget.http_count,'request':spec})
            result=transport.http(spec)
            saved={'request':spec,'response':{'status':result['status'],'body':result['body'][:65536]}}
            report['http'].append(saved); save_new(output/f'http-{budget.http_count:02d}.json',saved); checkpoint()
            if result['status']!=spec['status'] or len(result['body'].encode())>65536: raise StopRun('http_contract_failure')
            if 'detail' in spec and json.loads(result['body']).get('detail')!=spec['detail']: raise StopRun('health_or_disabled_model_failure')
    timer=threading.Timer(budget.remaining(True),lambda:os._exit(124)) if hard_deadline else None
    if timer: timer.daemon=True; timer.start()
    previous_umask=os.umask(0o077); sql_guard=isolated_sql(target['workspace'],journal); sql_guard.__enter__()
    try:
        journal.write({'event':'host_action_attempt','operation':'create_declared_synthetic_fixture','path':str(target['workspace'])})
        expected=fixture(target['workspace']); save_new(output/'fixture.json',expected)
        journal.write({'event':'host_action_finished','operation':'create_declared_synthetic_fixture'})
        from support_agent.production.config import Config
        from support_agent.production.volume import backup,restore,inspect
        for action in value['actions']:
            if action=='network_create':
                created=command(action,['network','create','--internal','--label','org.project5.run='+RUN_ID,value['docker_project']+'-isolated']).strip()
                if len(created)!=64 or not all(c in '0123456789abcdef' for c in created): raise StopRun('invalid_created_network_id')
                network=created; checkpoint()
            elif action.endswith('_create'):
                role=action.split('_')[0]; created=command(action,create_args(role,provenance['engine']['images'],network,ids.get('frontend'))).strip()
                if len(created)!=64 or not all(c in '0123456789abcdef' for c in created): raise StopRun('invalid_created_container_id')
                ids[role]=created; checkpoint()
            elif action.endswith('_start'):
                role=action.split('_')[0]; command(action,['start',ids[role]])
                if role=='backend': time.sleep(2)
            elif action in {'inspect_started','inspect_restarted'}:
                items=json.loads(command(action,['inspect',ids['frontend'],ids['backend']]))
                verify_containers(items,ids,network,provenance['engine']['images'])
                current={role:next(i['State']['StartedAt'] for i in items if i['Id']==cid) for role,cid in ids.items()}
                if action=='inspect_started': first_started=current
                elif current['backend']==first_started['backend'] or current['frontend']!=first_started['frontend']: raise StopRun('controlled_restart_unproven')
            elif action.startswith('probe_'):
                code={n:h for n,h in value['build_context_files'].items() if n.startswith('support_agent/')}
                observed=json.loads(command(action,['exec',ids['backend'],'python','-c',PROBE,json.dumps(code,sort_keys=True)])); verify_probe(observed,expected)
                if action=='probe_started': http_range(0,7)
                else: http_range(7,9)
            elif action=='backend_restart': command(action,['restart','--time','20',ids['backend']]); time.sleep(2)
            elif action.endswith('_stop'):
                role=action.split('_')[0]; stop_attempted.add(role); command(action,['stop','--time','20',ids[role]]); stopped.add(role)
            elif action=='inspect_stopped':
                items=json.loads(command(action,['inspect',ids['frontend'],ids['backend']]))
                if any(i['State']['Running'] or i['State']['ExitCode'] not in {0,143} for i in items): raise StopRun('unclean_shutdown')
                config=Config(data_dir=target['workspace']/'data'); destination=target['workspace']/'backup'; destination.mkdir(mode=0o700)
                before=inspect(config)
                journal.write({'event':'host_action_attempt','operation':'stopped_fixture_backup','path':str(destination)})
                backup(config,destination); journal.write({'event':'host_action_finished','operation':'stopped_fixture_backup'})
                journal.write({'event':'host_action_attempt','operation':'confirmed_stopped_fixture_restore','confirmation':'REPLACE-STOPPED-INDEX'})
                restored=restore(config,destination,'REPLACE-STOPPED-INDEX')
                journal.write({'event':'host_action_finished','operation':'confirmed_stopped_fixture_restore'})
                if restored!=before or restored['sha256']!=expected['index_sha256']: raise StopRun('backup_restore_failure')
                report['backup_restore']='PASS; isolated stopped fixture only'
            elif action=='backend_logs':
                log=command(action,['logs','--tail','50',ids['backend']])
                if 'Application shutdown complete' not in log: raise StopRun('graceful_shutdown_unproven')
            elif action in {'backend_remove','frontend_remove'}:
                role=action.split('_')[0]; remove_attempted.add(role); command(action,['rm',ids[role]]); removed.add(role)
            else:
                network_remove_attempted=True; command(action,['network','rm',network]); network=None
        if budget.http_count!=9 or budget.normal!=17: raise StopRun('uncertain_action_budget')
        if preservation(transport is None)['checked']!=history['checked'] or sha(target['claim'])!=sha(output/'claim.json'): raise StopRun('preservation_failure')
        budget.remaining(True)
        report.update(status='PASS',preserved_files=len(history['checked']),final_index_sha256=sha(target['database']))
    except BaseException as exc:
        budget.failed=True; report.update(status='STOPPED',failure={'type':type(exc).__name__,'reason':str(exc)})
        journal.write({'event':'stopped','failure':report['failure']}); checkpoint()
        # Only IDs returned by successful creates are eligible. No name lookup,
        # force deletion, anonymous volume deletion or host-wide cleanup exists.
        try:
            remaining={role:cid for role,cid in ids.items() if role not in removed}
            if remaining:
                items=json.loads(command('cleanup_inspect',['inspect',*remaining.values()],True))
                if len(items)!=len(remaining) or any(i['Id'] not in remaining.values() or i['Config']['Labels'].get('org.project5.run')!=RUN_ID for i in items):
                    raise StopRun('cleanup_ownership_mismatch')
                for role in ('backend','frontend'):
                    if role in remaining and role not in stop_attempted:
                        stop_attempted.add(role); command('cleanup_stop',['stop','--time','20',remaining[role]],True)
                for role in ('backend','frontend'):
                    if role in remaining and role not in remove_attempted:
                        remove_attempted.add(role); command('cleanup_remove',['rm',remaining[role]],True); removed.add(role)
            if network and not network_remove_attempted: command('cleanup_network',['network','rm',network],True); network=None
            uncertain=bool(set(ids)-removed & remove_attempted) or network_remove_attempted and network is not None
            report['cleanup']='attempted once for captured IDs; inspect retained journal for uncertain operations' if uncertain else 'complete for captured IDs'
        except BaseException as cleanup_error: report['cleanup']='incomplete; manual exact-ID inspection required: '+type(cleanup_error).__name__
    finally:
        try:
            if preservation(transport is None)['checked']!=history['checked']: raise StopRun('preservation_failure')
            if provenance['offline_proof']['source_sha256']!=sources() or provenance['commit']!=git('rev-parse','HEAD').decode().strip(): raise StopRun('preparation_source_changed')
            if sha(target['claim'])!=sha(output/'claim.json'): raise StopRun('consumed_claim_changed')
            report['preservation']='PASS'
        except BaseException: report.update(status='STOPPED',preservation='FAIL')
        checkpoint(); save_new(output/'report.json',report)
        save_new(output/'artifacts.sha256.json',{p.relative_to(output).as_posix():sha(p) for p in sorted(output.rglob('*')) if p.is_file()})
        if timer: timer.cancel()
        sql_guard.__exit__(None,None,None)
        os.umask(previous_umask)
    return report

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check-preparation',action='store_true')
    mode.add_argument('--execute-authorized-once',action='store_true')
    args=parser.parse_args(argv)
    if args.check_preparation:
        try: result=preflight()
        except (StopRun,OSError,ValueError,subprocess.SubprocessError) as exc:
            print(json.dumps({'status':'BLOCKED','reason':str(exc),'actual_runtime_executed':False,'claim_consumed':False})); return 2
        print(json.dumps({'status':'READY_FOR_SEPARATE_AUTHORIZATION','commit':result['commit'],'images':result['engine']['images'],'plan_sha256':PLAN_SHA,'claim_consumed':False})); return 0
    budget=Budget(); timer=threading.Timer(budget.remaining(True),lambda:os._exit(124)); timer.daemon=True; timer.start()
    try: result=execute(preflight(),budget)
    finally: timer.cancel()
    print(json.dumps({'status':result['status'],'actions':result['normal_actions'],'http_requests':budget.http_count}))
    return 0 if result['status']=='PASS' else 1

if __name__=='__main__': raise SystemExit(main())
