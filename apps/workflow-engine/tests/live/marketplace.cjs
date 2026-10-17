// Marketplace Trust & Payments: real Gemini + real browser UI, backend state asserted through the API.
// Driven by run_marketplace.py (isolated DB/ports, allow-listed env). Artifacts: screenshots/marketplace/ (not committed).
const {chromium,expect}=require(process.env.MARKETPLACE_ROOT+'/apps/workflow-studio/node_modules/@playwright/test');
const fs=require('fs');
const {execFileSync}=require('child_process');
const state=JSON.parse(fs.readFileSync(process.env.MARKETPLACE_STATE_FILE,'utf8'));
const credentials=JSON.parse(fs.readFileSync(process.env.MARKETPLACE_CREDENTIAL_FILE,'utf8'));
const SV=credentials.secrets;                       // synthetic secret values (assertions only)
const privateValues=Object.values(SV);
const ROOT=process.env.MARKETPLACE_ROOT;
const out=ROOT+'/screenshots/marketplace'+(process.env.MARKETPLACE_OUT?'/'+process.env.MARKETPLACE_OUT:'');fs.mkdirSync(out,{recursive:true});
const progressFile=state.tmp+'/progress.json';
const only=process.env.MARKETPLACE_ONLY?new Set(process.env.MARKETPLACE_ONLY.split(',')):null;
const skip=new Set((process.env.MARKETPLACE_SKIP||'').split(',').filter(Boolean));
const MAX_LLM=Number(process.env.MARKETPLACE_MAX_LLM||95);
const progress=fs.existsSync(progressFile)?JSON.parse(fs.readFileSync(progressFile,'utf8')):{};
const saveProgress=()=>fs.writeFileSync(progressFile,JSON.stringify(progress));
const redact=t=>privateValues.reduce((s,v)=>s.split(v).join('[REDACTED]'),String(t));
const report={model:state.model,provider:'real Gemini',database:state.dbName,steps:[],violations:[],leak_checks:{},notes:[]};
let browser,context,page;

// ---------------------------------------------------------------- API helpers (Node side, no browser policy)
async function raw(method,path,body,headers={}){
 const r=await fetch(state.engineUrl+(path.startsWith('/a/')||path.startsWith('/webhook')?'':'/api')+path,{method,headers:{'Content-Type':'application/json',...headers},body:body===undefined?undefined:(typeof body==='string'?body:JSON.stringify(body))});
 const text=await r.text();let parsed=text;try{parsed=text?JSON.parse(text):undefined}catch{}
 return {status:r.status,body:parsed,text,headers:r.headers};
}
async function api(method,path,body){const r=await raw(method,path,body);if(r.status<200||r.status>=300)throw Error(`${method} ${path}: ${r.status} ${redact(r.text).slice(0,600)}`);return r.body;}
const P=()=>progress.project;                       // {id,slug,...}
const pp=p=>`/projects/${P().id}${p}`;
async function tablePath(name){const t=(await api('GET',pp('/tables'))).find(x=>x.name===name);if(!t)throw Error('no table '+name);return pp('/tables/'+t.id);}
async function rowCount(name){return (await api('GET',(await tablePath(name))+'/rows?count=true&limit=1')).count;}
async function allRows(name){const base=await tablePath(name);const out=[];let cursor=null;for(let i=0;i<60;i++){const r=await api('GET',`${base}/rows?limit=100${cursor?'&cursor='+encodeURIComponent(cursor):''}`);out.push(...r.rows);cursor=r.next_cursor;if(!cursor)break;}return out;}
async function routes(){return api('GET',pp('/routes'));}
async function routeByName(name){const r=(await routes()).find(x=>x.name===name);if(!r)throw Error('no route '+name);return r;}
async function testRoute(name,{env='dev',query={},params={},body=null,headers={},apply=false}={}){const r=await routeByName(name);return api('POST',pp(`/routes/${r.id}/test`),{environment:env,query,params,headers,body,...(apply?{apply_schemas:true}:{})});}
async function publicCall(method,path,body,headers={}){return raw(method,`/p/${P().slug}${path}`,body,headers);}
function sql(query,params=[]){
 // direct SQL against the throwaway database only
 if(!/^workflows_test_e2e_[0-9a-f]{8}$/.test(state.dbName))throw Error('refusing database '+state.dbName);
 const code=`import json,sys,psycopg2
c=psycopg2.connect(host=${JSON.stringify(state.dbHost)},port=${state.dbPort},user=${JSON.stringify(state.dbUser)},password=${JSON.stringify(state.dbPassword)},dbname=${JSON.stringify(state.dbName)})
q,p=json.loads(sys.stdin.read())
with c, c.cursor() as cur:
    cur.execute(q,p)
    print(json.dumps(cur.fetchall(),default=str) if cur.description else '[]')`;
 return JSON.parse(execFileSync(process.env.MARKETPLACE_PYTHON,['-c',code],{input:JSON.stringify([query,params]),encoding:'utf8'}));
}

// ---------------------------------------------------------------- LLM accounting (engine-side hook, metadata only)
function llmEntries(){try{return fs.readFileSync(state.llmLog,'utf8').split('\n').filter(Boolean).map(l=>JSON.parse(l));}catch{return [];}}
const llmCount=()=>llmEntries().length;
const PROVIDER_RE=/quota|high demand|overloaded|unavailable|503|429|resource_exhausted|rate limit|malformed|MALFORMED|timed out|deadline|empty response|no result|internal error/i;

// ---------------------------------------------------------------- network + console policy (strict)
let expected=[];                                     // declared 4xx/5xx: {method,url:RegExp,status,seen}
const violations=[];
async function expectStatus(list,fn,waitMs=8000){
 const entries=list.map(e=>({...e,seen:0}));expected.push(...entries);
 try{const r=await fn();const end=Date.now()+waitMs;while(entries.some(e=>!e.seen)&&Date.now()<end)await new Promise(r=>setTimeout(r,100));
  for(const e of entries)if(!e.seen)violations.push(`declared HTTP ${e.status} ${e.method||'*'} ${e.url} never happened`);return r;}
 finally{expected=expected.filter(e=>!entries.includes(e));}
}
const leaks=[];                                      // responses that contained a secret value
function watchContext(ctx){
 ctx.on('response',async r=>{
  const url=r.url();if(!(url.startsWith(state.engineUrl)||url.startsWith(state.studioUrl)))return;
  const status=r.status(),method=r.request().method();
  if(status>=400){const a=expected.find(x=>x.status===status&&x.url.test(url)&&(!x.method||x.method===method));if(a)a.seen++;else violations.push(`unexpected HTTP ${status} ${method} ${url.replace(state.engineUrl,'<engine>').replace(state.studioUrl,'<studio>')}`);}
  if(method!=='OPTIONS'&&url.startsWith(state.engineUrl)){try{const t=await r.text();if(privateValues.some(v=>t.includes(v)))leaks.push(`${method} ${url.replace(state.engineUrl,'')}`);}catch{}}
 });
 const hook=p=>{
  p.on('console',m=>{const t=m.type(),x=m.text();if(t!=='error'&&t!=='warning'&&!/^Warning:/.test(x))return;if(/^Failed to load resource: the server responded with a status of \d+/.test(x))return;violations.push(`console.${t} on ${redact(p.url())}: ${redact(x).slice(0,500)}`);});
  p.on('pageerror',e=>violations.push(`pageerror on ${redact(p.url())}: ${redact(e.message).slice(0,500)}`));
 };
 ctx.pages().forEach(hook);ctx.on('page',hook);
}

// ---------------------------------------------------------------- step runner
async function step(id,title,fn,{llm=false}={}){
 if((only&&!only.has(id))||skip.has(id)){return;}
 const rec={id,title,status:'running',duration_ms:0,llm_calls:0,llm_errors:0,tokens:{in:0,out:0},retries:[],flagged_retry_only:false};report.steps.push(rec);
 const t0=Date.now();
 for(let attempt=0;attempt<3;attempt++){
  const l0=llmCount(),v0=violations.length,a0=Date.now();
  if(llm&&l0>=MAX_LLM){rec.status='fail';rec.error=`LLM budget wall reached (${l0}/${MAX_LLM})`;break;}
  try{
   await fn(attempt);
   const v=violations.slice(v0);if(v.length)throw Error('policy violations: '+v.join(' | '));
   if(leaks.length)throw Error('secret value found in response: '+leaks.join(', '));
   rec.status='pass';
   if(attempt>0)rec.flagged_retry_only=true;
   break;
  }catch(e){
   const entries=llmEntries().slice(l0);const provider=entries.some(x=>!x.ok)||PROVIDER_RE.test(e.message);
   const msg=redact(e.message).slice(0,900);
   if(llm&&provider&&attempt<2){rec.retries.push({attempt:attempt+1,error:msg,provider_errors:entries.filter(x=>!x.ok).map(x=>x.err+':'+(x.code||x.status||''))});console.log(`[${id}] provider-side failure, retry ${attempt+1}/2: ${msg.slice(0,200)}`);if(page)await page.screenshot({path:`${out}/${id}-retry${attempt+1}.png`}).catch(()=>{});continue;}
   rec.status='fail';rec.error=msg;
   if(page)await page.screenshot({path:`${out}/${id}-failure.png`}).catch(()=>{});
   break;
  }
 }
 rec.duration_ms=Date.now()-t0;rec.t0=t0;rec.t1=Date.now();
 console.log(`${rec.status==='pass'?'PASS':'FAIL'} ${id} ${title} (${(rec.duration_ms/1000).toFixed(1)}s, retries ${rec.retries.length}${rec.flagged_retry_only?' RETRY-ONLY':''})${rec.error?': '+rec.error.slice(0,400):''}`);
 return rec.status==='pass';
}
// per-step LLM accounting (computed after the fact from timestamps)
function attribute(){
 const ents=llmEntries();
 for(const rec of report.steps){if(rec.t0==null)continue;const mine=ents.filter(e=>e.t*1000>=rec.t0&&e.t*1000<=rec.t1);rec.llm_calls=mine.length;rec.llm_errors=mine.filter(e=>!e.ok).length;rec.tokens={in:mine.reduce((n,e)=>n+(e.in_tok||0),0),out:mine.reduce((n,e)=>n+(e.out_tok||0),0)};}
}
// ---------------------------------------------------------------- UI helpers
const S=()=>state.studioUrl+'/p/'+P().slug;
function events(text){return text.split(/\r?\n\r?\n/).flatMap(b=>{const d=b.split(/\r?\n/).filter(l=>l.startsWith('data:')).map(l=>l.slice(5).trimStart()).join('\n');try{return [JSON.parse(d)]}catch{return []}});}
async function streamCount(){return page.evaluate(()=>window.__liveStreams.length);}
async function nextStream(before,part,timeout=600000){
 await expect.poll(async()=>page.evaluate(({before,part})=>window.__liveStreams.slice(before).some(s=>s.url.includes(part)),{before,part}),{timeout}).toBe(true);
 return events(await page.evaluate(({before,part})=>window.__liveStreams.slice(before).find(s=>s.url.includes(part)).text,{before,part}));
}
// Send a prompt through the inline composer; resolves with the generator's SSE events (real model output).
async function generateUI(prompt){
 await expect(page.getByTestId('composer')).toBeVisible();
 const before=await streamCount();const input=page.getByTestId('composer-input');
 if(!await input.isVisible())await page.getByTestId('composer-submit').click();
 await input.fill(prompt);await input.press('Enter');
 const ev=await nextStream(before,'/modules/generate');
 const errors=ev.filter(e=>e.type==='error');if(errors.length)throw Error('generate error: '+JSON.stringify(errors).slice(0,500));
 if(!ev.find(e=>e.type==='result'))throw Error('no result: '+JSON.stringify(ev.slice(-2)).slice(0,500));
 return ev;
}
async function shot(name){await page.screenshot({path:`${out}/${name}.png`});}
async function toast(re,timeout=20000){await expect(page.getByText(re).first()).toBeVisible({timeout});}
async function editorText(){return page.getByTestId('code-editor').locator('.cm-line').evaluateAll(ls=>ls.map(l=>l.textContent??'').join('\n'));}
async function setSource(text){const c=page.getByTestId('code-editor').locator('.cm-content');await c.click();await page.keyboard.press('ControlOrMeta+a');await page.keyboard.press('Delete');await page.keyboard.insertText(text);}
async function saveEditor(){await page.getByTestId('code-editor').locator('.cm-content').click();await page.keyboard.press('ControlOrMeta+s');}
async function createTableUI(name,cols){
 await page.goto(`${S()}/data?dialog=new-table`);
 const d=page.getByTestId('new-table-dialog');await expect(d).toBeVisible();
 await d.getByTestId('new-table-name').fill(name);
 for(let i=0;i<cols.length;i++){
  if(i>0)await d.getByTestId('new-table-columns-add').click();
  await d.getByTestId(`new-table-columns-name-${i}`).fill(cols[i][0]);
  if(cols[i][1]!=='string'){await d.getByTestId(`new-table-columns-type-${i}`).click();await page.getByTestId(`new-table-columns-type-${i}-${cols[i][1]}`).click();}
 }
 await d.getByTestId('new-table-create').click();
 await expect(page).toHaveURL(new RegExp(`/data/${name}$`));
}
async function addSecretUI(key,value,envs,hosts){
 await page.goto(`${S()}/secrets`);
 await page.getByTestId('secret-add').click();
 await page.getByTestId('create-secret-key').fill(key);
 await page.getByTestId('create-secret-value').fill(value);
 for(const env of ['dev','prod']){const box=page.getByTestId('create-secret-env-'+env);const on=(await box.getAttribute('aria-checked'))==='true'||(await box.getAttribute('data-checked'))!==null;if(on!==envs.includes(env))await box.click();}
 for(const h of hosts){await page.getByTestId('create-secret-hosts-input').fill(h);await page.getByTestId('create-secret-hosts-input').press('Enter');}
 await page.getByTestId('create-secret-save').click();
 for(const env of envs)await expect(page.getByTestId(`secret-cell-${key}-${env}`)).toHaveAttribute('data-state','set');
}

// ================================================================= STEPS
const defs=require('./marketplace_steps.cjs');
(async()=>{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true});
 context=await browser.newContext({viewport:{width:1440,height:900}});
 // Only font rendering is localized; every application and AI request stays real.
 await context.route(/https:\/\/fonts\.(googleapis|gstatic)\.com\//,r=>r.fulfill({status:200,contentType:'text/css',body:''}));
 await context.addInitScript(()=>{
  window.__liveStreams=[];const orig=window.fetch.bind(window);
  window.fetch=async(input,init)=>{const r=await orig(input,init);const url=typeof input==='string'?input:input.url||String(input);
   if(url.includes('/modules/generate')||url.includes('/ai/app-builder/chat'))r.clone().text().then(text=>window.__liveStreams.push({url,text})).catch(()=>{});return r;};
 });
 watchContext(context);
 page=await context.newPage();
 const env={leaks,streamCount,nextStream,generateUI,events,state,SV,out,progress,saveProgress,report,raw,api,pp,P,tablePath,rowCount,allRows,routes,routeByName,testRoute,publicCall,sql,llmEntries,llmCount,expectStatus,step,S,shot,toast,editorText,setSource,saveEditor,createTableUI,addSecretUI,expect,get page(){return page},get context(){return context},violations,redact,fs};
 await defs.run(env);
 attribute();
})().catch(e=>{report.fatal=redact(e.message);console.log('FATAL',redact(e.message).slice(0,2000));process.exitCode=1;}).finally(async()=>{
 try{attribute();}catch{}
 const failed=report.steps.filter(s=>s.status!=='pass');
 report.llm_total=llmCount();report.llm_errors=llmEntries().filter(e=>!e.ok).length;
 report.violations=violations.map(redact);report.passed=!report.fatal&&failed.length===0&&violations.length===0;
 if(failed.length||violations.length)process.exitCode=1;
 fs.writeFileSync(out+'/report.json',redact(JSON.stringify(report,null,2)));
 console.log('\nSTEP SUMMARY');
 for(const s of report.steps)console.log(`${s.status.toUpperCase().padEnd(5)} ${s.id.padEnd(5)} ${s.title.slice(0,60).padEnd(60)} ${(s.duration_ms/1000).toFixed(1).padStart(6)}s llm=${s.llm_calls}${s.llm_errors?'(err '+s.llm_errors+')':''} retries=${s.retries.length}${s.flagged_retry_only?' RETRY-ONLY':''}`);
 console.log(`LLM calls total: ${report.llm_total} (errors ${report.llm_errors}); violations: ${violations.length}`);
 await browser?.close();
});
