const {chromium,expect}=require(process.env.DASHBOARD_ROOT+'/apps/workflow-studio/node_modules/@playwright/test');
const fs=require('fs');
const state=JSON.parse(fs.readFileSync(process.env.DASHBOARD_STATE_FILE,'utf8'));
const credentials=JSON.parse(fs.readFileSync(process.env.DASHBOARD_CREDENTIAL_FILE,'utf8'));
const privateValues=Object.values(credentials.secrets);
const out=process.env.DASHBOARD_ROOT+'/screenshots/postgres-dashboard';fs.mkdirSync(out,{recursive:true});
const report={model:state.model,database:state.dbName,provider:'real Gemini',checks:{},errors:[]};
let browser,page;
const {execFileSync}=require('child_process');
async function api(method,path,body){const r=await fetch(state.engineUrl+'/api'+path,{method,headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});const text=await r.text();if(!r.ok)throw Error(`${method} ${path}: ${r.status} ${text.slice(0,500)}`);return text?JSON.parse(text):null;}
function events(text){return text.split(/\r?\n\r?\n/).flatMap(b=>{const d=b.split(/\r?\n/).filter(l=>l.startsWith('data:')).map(l=>l.slice(5).trimStart()).join('\n');try{return [JSON.parse(d)]}catch{return []}});}
async function streamCount(){return page.evaluate(()=>window.__liveStreams.length);}
async function nextStream(before,part){await expect.poll(async()=>page.evaluate(({before,part})=>window.__liveStreams.slice(before).some(s=>s.url.includes(part)),{before,part}),{timeout:300000}).toBe(true);return events(await page.evaluate(({before,part})=>window.__liveStreams.slice(before).find(s=>s.url.includes(part)).text,{before,part}));}
async function generate(prompt,label){
 await expect(page.getByTestId('composer')).toBeVisible();
 const before=await streamCount();const input=page.getByTestId('composer-input');
 if(!await input.isVisible())await page.getByTestId('composer-submit').click();
 await input.fill(prompt);await input.press('Enter');
 const ev=await nextStream(before,'/modules/generate');
 const errors=ev.filter(e=>e.type==='error');if(errors.length)throw Error(label+': '+JSON.stringify(errors));
 const r=ev.find(e=>e.type==='result');if(!r)throw Error(label+': no result '+JSON.stringify(ev.slice(-2)));
 report.checks[label]={model:r.model,module:r.module_name,attempts:r.attempts,self_test:r.self_test,source:r.source};
 await expect(page.getByTestId('composer-result')).toBeVisible();
 await page.screenshot({path:`${out}/${label}.png`});
 await page.getByTestId('save-module').click();
 await expect(page.getByText(new RegExp(`Saved ${r.module_name}\\.py`))).toBeVisible({timeout:20000});
 return r;
}
(async()=>{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true});
 const context=await browser.newContext({viewport:{width:1440,height:900}});
 // Only font rendering is localized; every application and AI request remains real.
 await context.route(/https:\/\/fonts\.(googleapis|gstatic)\.com\//,r=>r.fulfill({status:200,contentType:'text/css',body:''}));
 await context.addInitScript(()=>{
  window.__liveStreams=[];const orig=window.fetch.bind(window);
  window.fetch=async(input,init)=>{const r=await orig(input,init);const url=typeof input==='string'?input:input.url||String(input);
   if(url.includes('/modules/generate')||url.includes('/ai/app-builder/chat'))r.clone().text().then(text=>window.__liveStreams.push({url,text}));return r;};
 });
 page=await context.newPage();
 context.on('page',p=>{p.on('pageerror',e=>report.errors.push(e.message));});
 page.on('pageerror',e=>report.errors.push(e.message));
 const p=await api('POST','/projects',{name:'Postgres Inventory Dashboard',slug:'gemini-live-'+Date.now()});report.project=p;
 await page.goto(`${state.studioUrl}/p/${p.slug}/backend/~new`);
 // Save actual database username/password/name using the Secrets UI in dev and prod.
 await page.goto(`${state.studioUrl}/p/${p.slug}/secrets`);
 const responses=[];page.on('response',async r=>{if(r.url().startsWith(state.engineUrl)&&r.request().method()!=='OPTIONS'){try{responses.push(await r.text())}catch{}}});
 for(const [key,value] of Object.entries(credentials.secrets)){
  await page.getByTestId('secret-add').click();
  await page.getByTestId('create-secret-key').fill(key);
  await page.getByTestId('create-secret-value').fill(value);
  await page.getByTestId('create-secret-hosts-input').fill('127.0.0.1');await page.getByTestId('create-secret-hosts-input').press('Enter');
  await page.getByTestId('create-secret-save').click();
  await expect(page.getByTestId(`secret-cell-${key}-dev`)).toHaveAttribute('data-state','set');
  await expect(page.getByTestId(`secret-cell-${key}-prod`)).toHaveAttribute('data-state','set');
 }
 const secrets=await api('GET',`/projects/${p.id}/secrets`);
 expect(secrets).toHaveLength(6);expect(secrets.every(s=>s.value===null)).toBe(true);
 expect(privateValues.some(v=>JSON.stringify(secrets).includes(v))).toBe(false);
 await page.reload();await expect(page.getByTestId('secret-cell-PG_PASSWORD-dev')).toHaveAttribute('data-state','set');
 await page.screenshot({path:out+'/00-saved-secrets.png'});report.checks.secrets={saved_via_ui:true,persist_after_reload:true,environments:['dev','prod'],keys:Object.keys(credentials.secrets),write_only:true};
 // Rotate the database password and replace both environment values through the UI.
 execFileSync(process.env.DASHBOARD_PYTHON,['-c',`import json,secrets,psycopg2
from pathlib import Path
from psycopg2 import sql
path=Path(${JSON.stringify(process.env.DASHBOARD_CREDENTIAL_FILE)})
cfg=json.loads(path.read_text());old=cfg['secrets']['PG_PASSWORD'];new=secrets.token_hex(24)
with psycopg2.connect(host=${JSON.stringify(state.tmp)},port=cfg['port'],dbname=cfg['secrets']['PG_DATABASE']) as c:
 with c.cursor() as q:q.execute(sql.SQL('ALTER ROLE {} PASSWORD {}').format(sql.Identifier(cfg['secrets']['PG_USER']),sql.Literal(new)))
try:
 psycopg2.connect(host='127.0.0.1',port=cfg['port'],dbname=cfg['secrets']['PG_DATABASE'],user=cfg['secrets']['PG_USER'],password=old)
 raise AssertionError('Old password still works')
except psycopg2.OperationalError:pass
cfg['secrets']['PG_PASSWORD']=new;path.write_text(json.dumps(cfg))
`]);
 const rotated=JSON.parse(fs.readFileSync(process.env.DASHBOARD_CREDENTIAL_FILE,'utf8')).secrets.PG_PASSWORD;privateValues.push(rotated);
 await page.locator('[data-testid="secret-row"]').filter({hasText:'PG_PASSWORD'}).getByTestId('secret-expand').click();
 for(const env of ['dev','prod']){
  const tid=`secret-replace-PG_PASSWORD-${env}`;
  await page.getByTestId(tid).click();await page.getByTestId(tid+'-input').fill(rotated);
  await page.getByTestId(tid+'-save').click();await expect(page.getByTestId(tid+'-input')).toHaveCount(0);
 }
 report.checks.secrets.rotation={replaced_via_ui:true,old_password_rejected:true};
 await page.screenshot({path:out+'/00b-rotated-secrets.png'});
 await page.goto(`${state.studioUrl}/p/${p.slug}/backend/~new`);
 console.log('Generating real Postgres backend through browser');
 const connection=`Use studio.nodes.postgres(connection_string=f"host=127.0.0.1 port=${credentials.port} user={studio.secret('PG_USER')} password={studio.secret('PG_PASSWORD')} dbname={studio.secret('PG_DATABASE')}", operation="query", query=...). Its response is a list whose first item has rows and rowCount. Only secret NAMES belong in code, never values. The real external Postgres table public.inventory already exists, columns id text, name text, price numeric, currency text. This is NOT a studio.db table. Do not declare tables or return hardcoded data.`;
 const generated=await generate(`Create module inventory_api with public GET /items operation listItems. ${connection} SELECT id,name,price,currency FROM inventory ORDER BY id. Return {"items": rows, "count": len(rows)}. Import api from studio and import studio.`, '01-generate');
 expect(generated.source).toContain('studio.secret');expect(generated.source).toContain('postgres');
 let routes=await api('GET',`/projects/${p.id}/routes`);let list=routes.find(r=>r.path==='/items');expect(list).toBeTruthy();
 let result=await api('POST',`/projects/${p.id}/routes/${list.id}/test`,{environment:'dev'});expect(result.status).toBe(200);expect(result.body.count).toBe(3);report.checks.generated_route=result;
 console.log('Refining real Postgres backend through browser');
 const refined=await generate(`Keep GET /items. Add public GET /items/summary operation inventorySummary. ${connection} Run SELECT count(*)::int AS count, min(currency) AS currency, sum(price) AS total FROM inventory. Return the first row directly. Keep module name inventory_api.`, '02-refine');
 routes=await api('GET',`/projects/${p.id}/routes`);const summary=routes.find(r=>r.path==='/items/summary');expect(summary).toBeTruthy();
 result=await api('POST',`/projects/${p.id}/routes/${summary.id}/test`,{environment:'dev'});expect(result.status).toBe(200);expect(result.body).toEqual({count:3,currency:'USD',total:66.25});report.checks.refined_route=result;
 for(const val of privateValues)expect(generated.source+refined.source).not.toContain(val);
 await page.goto(`${state.studioUrl}/p/${p.slug}?dialog=create-ui`);
 await page.getByTestId('create-ui-name').fill('Gemini Inventory');
 await page.getByTestId('create-ui-prompt').fill('Build a polished inventory dashboard using the attached listItems and inventorySummary typed client methods. Actually call the backend on mount. Show the actual inventory item names, currency, count, total and prices from the responses. Include a working Refresh button. Use React useState/useEffect, ordinary HTML and Tailwind. Only write App.tsx and import api from ./api/client. Do not mock any data. Do not request new endpoints. Keep the implementation under 180 lines. For verification, render each item name from the API in data-testid="live-item-name", render the API item count in data-testid="live-items-count", and render summary.currency in data-testid="live-currency". Show a loading state until both requests finish. Do not place Blue mug or USD in static labels or defaults.');
 const before=await streamCount();await page.getByTestId('create-ui-submit').click();
 console.log('Testing real Create UI in browser');
 const ev=await nextStream(before,'/ai/app-builder/chat');const failures=ev.filter(e=>e.type==='error');if(failures.length)throw Error('Create UI: '+JSON.stringify(failures));
 const appId=new URL(page.url()).pathname.split('/').pop();
 await expect.poll(async()=>{const a=await api('GET',`/apps/${appId}`);return a.files?.some(f=>f.path==='App.tsx')},{timeout:30000}).toBe(true);
 const frame=page.frameLocator('[data-testid="preview-iframe"]');await expect(frame.getByTestId('live-item-name')).toHaveText(['Notebook','Desk lamp','Blue mug'],{timeout:45000});await expect(frame.getByTestId('live-items-count')).toHaveText('3');await expect(frame.getByTestId('live-currency')).toHaveText('USD');
 await page.screenshot({path:`${out}/03-create-ui.png`});
 const app=await api('GET',`/apps/${appId}`);report.checks.create_ui={appId,files:app.files.map(f=>f.path),events:ev.map(e=>e.type)};report.generated_files=app.files;
 // Change the actual external database, leaving generated backend/UI code untouched.
 execFileSync(process.env.DASHBOARD_PYTHON,['-c',`import json,psycopg2
from pathlib import Path
cfg=json.loads(Path(${JSON.stringify(process.env.DASHBOARD_CREDENTIAL_FILE)}).read_text())
with psycopg2.connect(host=${JSON.stringify(state.tmp)},port=cfg['port'],dbname=cfg['secrets']['PG_DATABASE']) as c:
 with c.cursor() as q:
  q.execute("UPDATE inventory SET currency='EUR'")
  q.execute("UPDATE inventory SET name='Green mug' WHERE id='mug'")
  q.execute("INSERT INTO inventory VALUES ('pen','Fountain pen',10,'EUR')")
`]);
 const hits=[];page.on('response',r=>{if(r.url().includes(`/api/p/${p.slug}/`))hits.push({url:r.url(),status:r.status()});});
 await frame.getByRole('button',{name:/refresh/i}).first().click();await expect.poll(()=>hits.filter(x=>x.status===200).length,{timeout:20000}).toBeGreaterThanOrEqual(2);await expect(frame.getByTestId('live-item-name')).toHaveText(['Notebook','Desk lamp','Green mug','Fountain pen']);await expect(frame.getByTestId('live-currency')).toHaveText('EUR');await expect(frame.getByTestId('live-items-count')).toHaveText('4');report.checks.preview_refresh={responses:hits,changed_backend_data_rendered:true};await page.screenshot({path:out+'/03b-refresh.png'});
 const published=await api('POST',`/apps/${appId}/publish`,{slug:'gemini-live-'+Date.now(),access:'public'});
 const pub=await context.newPage();await pub.goto(state.engineUrl+'/a/'+published.slug);await expect(pub.getByTestId('live-item-name')).toHaveText(['Notebook','Desk lamp','Green mug','Fountain pen'],{timeout:30000});await expect(pub.getByTestId('live-items-count')).toHaveText('4');await expect(pub.getByTestId('live-currency')).toHaveText('EUR');await pub.screenshot({path:`${out}/04-published.png`});report.checks.published={slug:published.slug,works:true};
 for(const value of privateValues){expect(JSON.stringify(app.files)).not.toContain(value);expect(responses.some(t=>t.includes(value))).toBe(false);expect(await pub.locator('body').innerText()).not.toContain(value);}
 report.checks.privacy={no_secret_values_in_generated_files_or_browser_responses:true};
 expect(report.errors).toEqual([]);report.passed=true;console.log('PASS: real Postgres data, UI-saved credentials, real Gemini Generate/Refine/Create UI, refresh and publish');
})().catch(async e=>{report.passed=false;report.failure=privateValues.reduce((t,v)=>t.split(v).join('[REDACTED]'),e.message);console.log('Live browser validation failed:',report.failure.slice(0,1500));if(page)await page.screenshot({path:out+'/failure.png'}).catch(()=>{});process.exitCode=1;}).finally(async()=>{fs.writeFileSync(out+'/report.json',privateValues.reduce((t,v)=>t.split(v).join('[REDACTED]'),JSON.stringify(report,null,2)));await browser?.close();});
