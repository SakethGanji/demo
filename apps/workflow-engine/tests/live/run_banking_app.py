import json, os, pathlib, secrets, socket, subprocess, tempfile, time, signal, sys
import httpx
from banking_db import BankingDatabase as DemoDatabase

ROOT=pathlib.Path(__file__).resolve().parents[4]
ENGINE=ROOT/'apps/workflow-engine'
STUDIO=ROOT/'apps/workflow-studio'
PY=sys.executable
assert os.environ.get('GEMINI_API_KEY'), 'Load the key as an environment variable first'
model=os.environ.get('WORKFLOW_MODULE_GEN_MODEL','gemini-3.5-flash')
db='workflows_test_e2e_'+secrets.token_hex(4)
tmp=pathlib.Path(tempfile.mkdtemp(prefix='gemini-live-'))
base={k:v for k,v in os.environ.items() if k in ('PATH','HOME','LANG','HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY','http_proxy','https_proxy','all_proxy','no_proxy','SSL_CERT_FILE','SSL_CERT_DIR','REQUESTS_CA_BUNDLE','CURL_CA_BUNDLE','CHROMIUM_PATH','BANKING_BACKEND_PROOF','BANKING_UI_PROOF','BANKING_REFINEMENT_FILE','BANKING_REPLAY_UI')}
base['PATH']=str(STUDIO/'node_modules/.bin')+':'+base.get('PATH','/usr/bin:/bin')
subprocess.run([PY,'tests/e2e_stack.py','up'],cwd=ENGINE,env={**base,'TEST_DB_NAME':db},capture_output=True,text=True,check=True)
def port():
 with socket.socket() as s:
  s.bind(('127.0.0.1',0));return s.getsockname()[1]
ep,vp=port(),port()
assert ep!=8000 and vp!=5173
env={**base,'HOME':str(tmp),'WORKFLOW_DB_HOST':'127.0.0.1','WORKFLOW_DB_PORT':'5433','WORKFLOW_DB_USER':'workflow','WORKFLOW_DB_PASSWORD':'workflow','WORKFLOW_DB_NAME':db,'WORKFLOW_DISABLE_DOTENV':'1','SEED':'0','WORKFLOW_RELOAD':'false','WORKFLOW_LOG_LEVEL':'warning','GEMINI_API_KEY':os.environ['GEMINI_API_KEY'],'WORKFLOW_MODULE_GEN_MODEL':model,'WORKFLOW_APP_BUILDER_MODEL':model,'WORKFLOW_MODULE_GEN_TIMEOUT_S':'240'}
assert not any(k.startswith('WORKFLOW_FAKE') for k in env)
from cryptography.fernet import Fernet
env['WORKFLOW_ENCRYPTION_KEY']=Fernet.generate_key().decode()
procs=[]
demo=DemoDatabase(tmp)
def start(args,cwd,env,name):
 f=open(tmp/name,'w');p=subprocess.Popen(args,cwd=cwd,env=env,stdout=f,stderr=f,start_new_session=True);procs.append(p);return p
def ready(url,p):
 with httpx.Client(trust_env=False) as c:
  for _ in range(160):
   if p.poll() is not None:raise RuntimeError('Service exited; inspect local log')
   try:
    if c.get(url).status_code==200:return
   except httpx.HTTPError:pass
   time.sleep(.25)
 raise RuntimeError('Service readiness timeout')
try:
 demo.setup()
 base['DASHBOARD_CREDENTIAL_FILE']=str(demo.path)
 engine=start([PY,'-m','uvicorn','src.main:app','--app-dir',str(ENGINE),'--host','127.0.0.1','--port',str(ep)],tmp,env,'engine.log')
 ready(f'http://127.0.0.1:{ep}/health',engine)
 with httpx.Client(trust_env=False) as c:
  projects=c.get(f'http://127.0.0.1:{ep}/api/projects').json()
  assert [p['id'] for p in projects]==['proj_default']
 vite=start([str(STUDIO/'node_modules/.bin/vite'),'--host','127.0.0.1','--port',str(vp),'--strictPort'],STUDIO,{**base,'VITE_WORKFLOW_API_URL':f'http://127.0.0.1:{ep}'},'vite.log')
 ready(f'http://127.0.0.1:{vp}',vite)
 state={'engineUrl':f'http://127.0.0.1:{ep}','studioUrl':f'http://127.0.0.1:{vp}','model':model,'dbName':db,'tmp':str(tmp)}
 state_path=tmp/'state.json';state_path.write_text(json.dumps(state))
 base['DASHBOARD_STATE_FILE']=str(state_path)
 base['DASHBOARD_ROOT']=str(ROOT)
 base['DASHBOARD_PYTHON']=PY
 print('Real Gemini browser stack ready:',model,db,flush=True)
 result=subprocess.run(['node',str(pathlib.Path(__file__).with_name('banking_app.cjs'))],env=base,text=True)
 raise SystemExit(result.returncode)
finally:
 demo.stop()
 for p in reversed(procs):
  if p.poll() is None:
   os.killpg(p.pid,signal.SIGTERM)
   try:p.wait(timeout=15)
   except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL)
 subprocess.run([PY,'tests/e2e_stack.py','down','--db',db],cwd=ENGINE,env=base,capture_output=True)
 key=os.environ['GEMINI_API_KEY']
 for p in tmp.glob('*.log'):
  p.write_text(p.read_text().replace(key,'[REDACTED]').replace(demo.password,'[REDACTED]'))
