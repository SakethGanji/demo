const {chromium,expect}=require(process.env.DASHBOARD_ROOT+'/apps/workflow-studio/node_modules/@playwright/test');
const fs=require('fs');
const state=JSON.parse(fs.readFileSync(process.env.DASHBOARD_STATE_FILE,'utf8'));
const credentials=JSON.parse(fs.readFileSync(process.env.DASHBOARD_CREDENTIAL_FILE,'utf8'));
const privateValues=Object.values(credentials.secrets);
const full=process.env.BANKING_FULL_E2E==='1';
const caseworkExample=process.env.BANKING_CASEWORK_EXAMPLE==='1';
const out=process.env.DASHBOARD_ROOT+(full?'/screenshots/full-banking-app':'/screenshots/banking-app');fs.mkdirSync(out,{recursive:true});
const report={model:process.env.BANKING_EXAMPLE==='1' ? 'retained source; no AI calls' : state.model,database:state.dbName,provider:process.env.BANKING_EXAMPLE==='1' ? 'retained generated backend and designed UI' : 'real Gemini',checks:{},errors:[]};
report.ai_calls_this_run=!(caseworkExample||process.env.BANKING_REPLAY_UI==='1'||process.env.BANKING_EXAMPLE==='1');
report.retained_generated_casework=caseworkExample;
if(caseworkExample){report.model="retained Gemini-generated/refined source";report.provider="exact-source replay; no provider calls";}
report.resumed_sources=process.env.BANKING_FULL_RESUME==='1';
let browser,page;
const {execFileSync}=require('child_process');
async function api(method,path,body){const r=await fetch(state.engineUrl+'/api'+path,{method,headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});const text=await r.text();if(!r.ok)throw Error(`${method} ${path}: ${r.status} ${text.slice(0,500)}`);return text?JSON.parse(text):null;}
function events(text){return text.split(/\r?\n\r?\n/).flatMap(b=>{const d=b.split(/\r?\n/).filter(l=>l.startsWith('data:')).map(l=>l.slice(5).trimStart()).join('\n');try{return [JSON.parse(d)]}catch{return []}});}
async function streamCount(){return page.evaluate(()=>window.__liveStreams.length);}
async function nextStream(before,part){await expect.poll(async()=>page.evaluate(({before,part})=>window.__liveStreams.slice(before).some(s=>s.url.includes(part)),{before,part}),{timeout:600000}).toBe(true);return events(await page.evaluate(({before,part})=>window.__liveStreams.slice(before).find(s=>s.url.includes(part)).text,{before,part}));}
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
 const apiHits=[];context.on('response',r=>{if(r.url().includes('/api/p/'))apiHits.push({url:r.url(),status:r.status()});});
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
 const p=await api('POST','/projects',{name:'Banking Operations Command Center',slug:'gemini-live-'+Date.now()});report.project=p;
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
 await page.goto(`${state.studioUrl}/p/${p.slug}/backend/~new`);
 console.log((caseworkExample || process.env.BANKING_BACKEND_PROOF || process.env.BANKING_EXAMPLE==='1') ? 'Reusing generated Postgres backend against a fresh database' : 'Generating real Postgres backend through browser');
 const expected=JSON.parse(fs.readFileSync(state.tmp+'/banking-expected.json','utf8'));
 const connection=`Call studio.nodes.postgres(connection_string=f"host=127.0.0.1 port=${credentials.port} user={studio.secret('PG_USER')} password={studio.secret('PG_PASSWORD')} dbname={studio.secret('PG_DATABASE')}",operation="query",query=sql,query_parameters=params). The first result item has rows. No direct database drivers. No studio.db tables. External Postgres tables already exist. Use parameterized SQL with %s placeholders, never interpolate request inputs into SQL. Dates may be cast to text. Import api, HTTPError from studio and import studio. Secret NAMES only.`;
 const schema=`bank_branches(id int,name text,region text); bank_accounts(id int,name text,branch_id int,balance numeric); bank_transactions(id text,account_id int,amount numeric,status text,booked_at date,channel text,reference text); bank_cases(id text,transaction_id text,severity text,owner text,state text,reason text); bank_events(id int,transaction_id text,occurred_at timestamp,message text). Join transactions.account_id=accounts.id and accounts.branch_id=branches.id; cases/events.transaction_id=transactions.id. status is settled/pending/exception. All data synthetic.`;
 const contract=`Create module banking_ops with these public GET routes and exact operation names/output contracts:
 /ops/summary name opsSummary: {transactions:int,volume:number,exceptions:int,pending:int,accounts:int,branches:int}. Aggregate real database tables, no hardcoded counts.
 /ops/transactions name opsTransactions: query status (empty/all means all), region (empty/all means all), search (case-insensitive id, reference or account name), page (>=1), page_size (1..50 default25). Output {items:[{id,account_id,account_name,branch_name,region,amount,status,booked_at,channel,reference}],total:int,page:int,page_size:int}. Apply filters on server, ORDER BY t.id DESC, limit/offset. total must use identical filters. Clamp invalid page values, don't crash. Cast dates to text. Use fixed SQL with (%s='' OR ...) clauses. Reject no filters safely; don't invent rows.
 /ops/transactions/{id} name opsTransaction: req.params['id']; {transaction:{same fields as list},events:[{id,occurred_at,message}],cases:[{id,severity,owner,state,reason}]}. Return HTTPError(404,{'error':'not_found'}) if missing. Join account and branch.
 /ops/trends name opsTrends: {items:[{day:text,volume:number,count:int}]} GROUP BY booked_at ORDER BY booked_at; date::text as day.
 /ops/accounts name opsAccounts: {items:[{id,name,branch_name,region,balance,transaction_count:int}]} for all accounts joined branches and transaction counts; ORDER BY id.
 /ops/cases name opsCases: {items:[{id,transaction_id,severity,owner,state,reason,amount,account_name}]} joined transactions/accounts, ORDER BY case id DESC.
 Every handler accepts req. Keep helper query and filter functions concise. Return all routes in one module, don't declare new tables.`;
 let generated;
 if(caseworkExample || process.env.BANKING_BACKEND_PROOF || process.env.BANKING_EXAMPLE==='1'){
  const proof=(caseworkExample||process.env.BANKING_EXAMPLE==='1') ? {checks:{'01-generate':{module:'banking_ops',source:fs.readFileSync(process.env.DASHBOARD_ROOT+(caseworkExample?'/examples/banking-casework/banking_ops.py':'/examples/banking-operations/banking_ops.py'),'utf8').replace(/DB_PORT = \d+/,`DB_PORT = ${credentials.port}`)}}} : JSON.parse(fs.readFileSync(process.env.BANKING_BACKEND_PROOF,'utf8'));
  const prior=full&&proof.checks['01b-refine']?proof.checks['01b-refine']:proof.checks['01-generate'];
  generated={...prior,source:prior.source.replace(/port=\d+/,`port=${credentials.port}`)};
  await api('POST',`/projects/${p.id}/modules`,{name:generated.module,source:generated.source});
  report.checks['01-generate']={...generated,reused_real_generated_source:true,retargeted_isolated_port:true};
 }else generated=await generate(`${connection} Schema: ${schema} ${contract}`,'01-generate');
 expect(generated.source).toContain('studio.secret');expect(generated.source).toContain('postgres');
 if(full&&!caseworkExample&&!process.env.BANKING_BACKEND_PROOF){
  console.log('Refining generated backend with transactional case updates and audit history');
  generated=await generate(`Keep all six GET routes and their exact contracts from this module. Add PATCH /ops/cases/{id} public name opsUpdateCase and GET /ops/cases/{id}/actions public name opsCaseActions. ${connection}
  New existing table bank_case_actions(id bigserial,case_id text REFERENCES bank_cases(id),state text,owner text,note text,occurred_at timestamptz DEFAULT now()). The DB role has UPDATE on bank_cases, SELECT/INSERT on bank_case_actions and sequence usage; it cannot change transaction amounts or delete tables.
  PATCH accepts req.body {state,owner,note}. Use a shared validation helper: state must be exactly open/in_review/resolved, owner must be trimmed nonempty <=80 chars, note trimmed nonempty <=500 chars. Reject non-dict/missing/non-string fields with HTTPError(422,{'error':'invalid_case_update'}). Do not silently coerce bad values. Validate before any database write. Do the update and audit insert atomically in ONE parameterized SQL statement: WITH changed AS (UPDATE bank_cases SET state=%s, owner=%s WHERE id=%s RETURNING id,state,owner) INSERT INTO bank_case_actions(case_id,state,owner,note) SELECT id,state,owner,%s FROM changed RETURNING case_id,state,owner,note,occurred_at::text. Bind [state,owner,req.params['id'],note]. If no returned rows raise HTTPError(404,{'error':'case_not_found'}). Return {case: first_row}. Never interpolate request input into SQL. Never set bank_transactions.status during a case update.
  GET actions: verify case exists, else HTTPError(404,...). Return {items:[{id,case_id,state,owner,note,occurred_at:text}]} filtered by case_id and ORDER BY id DESC. Keep module banking_ops. Use shared _query, _connection and validation helpers so dependencies are inspectable. No tables need creation.`, '01b-refine');
 }
 const routes=await api('GET',`/projects/${p.id}/routes`);
 const routeByPath=Object.fromEntries(routes.map(r=>[r.path,r]));
 for(const path of ['/ops/summary','/ops/transactions','/ops/transactions/{id}','/ops/trends','/ops/accounts','/ops/cases'])expect(routeByPath[path]).toBeTruthy();
 async function testRoute(path,query={},params={},apply=false){
  const r=await api('POST',`/projects/${p.id}/routes/${routeByPath[path].id}/test`,{environment:'dev',query,params,apply_schemas:apply});
  expect(r.status,JSON.stringify(r)).toBe(200);return r.body;
 }
 expect(await testRoute('/ops/summary',{},{},true)).toEqual({transactions:expected.transactions,volume:expected.volume,exceptions:expected.exceptions,pending:expected.pending,accounts:40,branches:8});
 const first=await testRoute('/ops/transactions',{},{},true);expect(first.total).toBe(600);expect(first.items).toHaveLength(25);expect(first.items[0].id).toBe('TX-000600');
 const second=await testRoute('/ops/transactions',{page:'2'});expect(second.items[0].id).toBe('TX-000575');
 const exceptions=await testRoute('/ops/transactions',{status:'exception'});expect(exceptions.total).toBe(100);expect(exceptions.items.every(t=>t.status==='exception')).toBe(true);
 const search=await testRoute('/ops/transactions',{search:'TX-000600'});expect(search.total).toBe(1);expect(search.items[0].id).toBe('TX-000600');
 const hostile=await testRoute('/ops/transactions',{search:"' OR 1=1 --"});expect(hostile.total).toBe(0);
 const detail=await testRoute('/ops/transactions/{id}',{},{id:'TX-000600'},true);expect(detail.events).toHaveLength(2);expect(detail.cases).toHaveLength(1);expect(detail.transaction.account_name).toBe('Synthetic Client 040');
 const trends=await testRoute('/ops/trends',{},{},true);expect(trends.items).toHaveLength(15);expect(trends.items.reduce((n,t)=>n+t.count,0)).toBe(600);
 expect((await testRoute('/ops/accounts',{},{},true)).items).toHaveLength(40);expect((await testRoute('/ops/cases',{},{},true)).items).toHaveLength(100);
 if(full){
  const update=routes.find(r=>r.name==='opsUpdateCase'),actions=routes.find(r=>r.name==='opsCaseActions');expect(update).toBeTruthy();expect(actions).toBeTruthy();
  await api('PUT',`/projects/${p.id}/routes/${update.id}`,{request_schema:{type:'object',properties:{state:{type:'string',enum:['open','in_review','resolved']},owner:{type:'string'},note:{type:'string'}},required:['state','owner','note']}});
  let result=await api('POST',`/projects/${p.id}/routes/${update.id}/test`,{environment:'dev',params:{id:'CASE-000600'},body:{state:'in_review',owner:'Avery Chen',note:'Initial independent API verification'},apply_schemas:true});expect(result.status).toBe(200);
  result=await api('POST',`/projects/${p.id}/routes/${actions.id}/test`,{environment:'dev',params:{id:'CASE-000600'},apply_schemas:true});expect(result.status).toBe(200);expect(result.body.items).toHaveLength(1);
  for(const body of [{state:'deleted',owner:'Avery',note:'invalid'},{state:'open',owner:'',note:'invalid'},{state:'open',owner:'Avery',note:''}]){
   const bad=await api('POST',`/projects/${p.id}/routes/${update.id}/test`,{environment:'dev',params:{id:'CASE-000600'},body});expect(bad.status).toBe(422);
  }
  const absent=await api('POST',`/projects/${p.id}/routes/${update.id}/test`,{environment:'dev',params:{id:'MISSING'},body:{state:'open',owner:'Avery',note:'must not write'}});expect(absent.status).toBe(404);
  const model=await api('POST',`/projects/${p.id}/modules/code-map`,{source:generated.source});expect(model.valid).toBe(true);expect(model.functions.length).toBeGreaterThanOrEqual(10);expect(model.edges.length).toBeGreaterThanOrEqual(6);report.checks.code_map={functions:model.functions.length,edges:model.edges.length,source_hash:model.source_hash};
  if(caseworkExample||process.env.BANKING_BACKEND_PROOF){await page.goto(`${state.studioUrl}/p/${p.slug}/backend/${generated.module}`);await expect(page.getByTestId('code-editor')).toBeVisible();}
  await page.getByTestId('backend-view-visual').click();await expect(page.getByTestId('map-sync')).toContainText('Synced with current code');await expect(page.locator('.react-flow__edge')).toHaveCount(model.edges.length);await page.screenshot({path:out+'/02-native-code-map.png'});
 }
 report.checks.backend={routes:routes.length,joined_detail:true,pagination:true,filters:true,injection_input_matched_zero:true,trends:15,accounts:40,cases:100};
 const client=await fetch(state.engineUrl+`/api/projects/${p.id}/client.ts?environment=dev`).then(r=>r.text());
 fs.writeFileSync(out+'/generated-client.ts',client);
 for(const val of privateValues)expect(generated.source).not.toContain(val);

 let uiPrompt=`Build a complete polished banking operations app named Meridian Operations with sidebar navigation, header showing Synthetic data / USD, and four views Overview, Transactions, Accounts, Exceptions. Use the six attached ops* typed client methods, real data only. Read their exact typed signatures. Plain React/HTML/Tailwind, inline SVG/CSS for charts, no external UI/chart libs. Only write App.tsx; import api from ./api/client. You may write up to 450 concise lines. No hardcoded metrics or rows. User-supplied API values are not trusted HTML.
 Overview: live transaction volume/count, pending, exceptions, accounts/branches KPI cards; daily volume bar chart using opsTrends and day/amount tooltips; recent transaction table; exception queue. data-testid kpi-transactions contains ONLY raw count from summary; kpi-exceptions only raw count; volume-chart on chart; trend-bar on each of15 bars, with title day/volume.
 Sidebar buttons data-testid nav-overview, nav-transactions, nav-accounts, nav-exceptions.
 Transactions: server-filtered and paginated table using opsTransactions. Native select data-testid status-filter options all/settled/pending/exception; native select region-filter options all/Americas/EMEA/APAC. Search input data-testid transaction-search. Native button data-testid apply-filters submits current inputs, resets page1 and requests server. Each table row data-testid transaction-row and data-id actual transaction.id. data-testid transactions-total contains ONLY raw total count; data-testid current-page contains ONLY page number. Buttons data-testid page-next and page-prev correctly disabled at bounds; 25 rows/page. Each row is clickable or has a Details button; fetch opsTransaction with id and show a slide-over/dialog data-testid transaction-detail containing actual id/account name/region/amount/status, real audit event messages and real case reasons. data-testid detail-close closes it.
 Accounts: joined client/branch/region/balance/transaction counts from opsAccounts, table rows data-testid account-row, 40 rows.
 Exceptions: joined case severity/owner/state/reason/amount and link to transaction detail using opsCases; rows data-testid case-row, 100rows.
 Refresh button data-testid app-refresh refreshes the current view and summary. All requests have visible loading/error/retry states and empty-state data-testid empty-state when filtered list empty. Changing filters or pagination must request backend, not filter25 rows locally. Guard race conditions and cancel stale requests on filter changes. Start on Overview and fetch data. Keep money formatted USD. Use responsive layout with no horizontal page overflow. Keyboard accessible sidebar, forms and detail close. A compact footer identifies read-only database access. Do not invent workflows or endpoints.`;
 if(full)uiPrompt=uiPrompt.replace('A compact footer identifies read-only database access.','A compact footer identifies synthetic banking data; case updates are supported.').replace('You may write up to 450 concise lines.','You may write up to 650 concise lines.').concat(`
  Add a complete case-management workflow to Exceptions using opsUpdateCase and opsCaseActions. Every case-row has a native button data-testid={'edit-case-'+case.id} labeled Manage case. Opening it shows a keyboard-accessible dialog data-testid=case-editor containing the actual case id and current owner/state. Native select data-testid=case-state has values open/in_review/resolved. Input data-testid=case-owner, textarea data-testid=case-note (required resolution note, starts blank), buttons case-save and case-cancel. Show existing audit actions from opsCaseActions in data-testid=case-history including actual owner,note,state. Validate trimmed owner/note before submit; blank fields show visible role=alert text and do not close dialog. Call opsUpdateCase using exact typed client body/path signatures, disable save while pending, display backend error inline, and on success show data-testid=case-saved confirmation, refresh case rows and fetch history. Show updated owner/state in row. Never pretend a failed save succeeded. Do not hardcode case values. Escape closes and restores focus. Theme toggle data-testid=theme-toggle switches full app light/dark styles, initially light. Empty and error/retry states must be visible and recover after a real database outage. Keep all original views, charts and verification hooks. Do not omit the case editor, responsive navigation or these interactions in order to shorten code.`);
 let before,ev,appId,replayedEntry;
 if(caseworkExample || process.env.BANKING_UI_PROOF || process.env.BANKING_EXAMPLE==='1'){
  const proof=(caseworkExample||process.env.BANKING_EXAMPLE==='1') ? {generated_files:[{path:'App.tsx',content:fs.readFileSync(process.env.DASHBOARD_ROOT+(caseworkExample?'/examples/banking-casework/App.tsx':'/examples/banking-operations/App.tsx'),'utf8')}]} : JSON.parse(fs.readFileSync(process.env.BANKING_UI_PROOF,'utf8'));
  const seeded=await api('POST','/apps',{name:'Banking Operations',project_id:p.id,definition:{nodes:[],rootNodeId:null,studio:{route_ids:routes.map(route=>route.id)}}});appId=seeded.id;
  await api('PUT',`/apps/${appId}`,{files:proof.generated_files,source_code:proof.generated_files.find(f=>f.path==='App.tsx').content,create_version:true});
  await page.goto(`${state.studioUrl}/p/${p.slug}/apps/${appId}`);
  if(caseworkExample || process.env.BANKING_REPLAY_UI==='1' || process.env.BANKING_EXAMPLE==='1'){
   ev=[];replayedEntry=proof.generated_files.find(f=>f.path==='App.tsx');report.replayed_real_generated_ui=process.env.BANKING_EXAMPLE!=='1';report.replayed_retained_designed_ui=process.env.BANKING_EXAMPLE==='1';
  }else{
  await expect(page.getByTestId('chat-input')).toBeVisible();before=await streamCount();
  await page.getByTestId('chat-input').fill(process.env.BANKING_REFINEMENT_FILE ? fs.readFileSync(process.env.BANKING_REFINEMENT_FILE,'utf8') : `Fix this existing generated app to use the now-applied typed API response contracts. Read api/client.ts and App.tsx first; use edit_files or write_files. The backend was tested and is correct. Never change the backend or client. Summary is exactly {transactions,volume,exceptions,pending,accounts,branches}, not transactionsCount/totalTransactions. opsTransaction returns {transaction:{id,account_name,region,amount,status,...},events:[{id,occurred_at,message}],cases:[{id,reason,...}]}; use detail.transaction for metadata and detail.events for audit. opsTrends returns {items:[{day,volume,count}]}; opsAccounts/opsCases return {items:[...]}. opsTransactions takes query directly (not {query:...}) and returns {items,total,page,page_size}; fields are snake_case (account_name,branch_name,booked_at,transaction_count). Preserve all views, chart and testids. Every feature in the following original specification must actually use live responses, not invented aliases or dummy defaults. ${uiPrompt}`);
  await page.getByTestId('chat-send').click();console.log('Refining real generated banking UI against applied response schemas');
  ev=await nextStream(before,'/ai/app-builder/chat');report.reused_real_generated_ui=true;
  }
 }else{
  await page.goto(`${state.studioUrl}/p/${p.slug}?dialog=create-ui`);
  await page.getByTestId('create-ui-name').fill('Banking Operations');
  await page.getByTestId('create-ui-prompt').fill(uiPrompt);
  before=await streamCount();await page.getByTestId('create-ui-submit').click();console.log('Generating full banking app via real Gemini');
  ev=await nextStream(before,'/ai/app-builder/chat');appId=new URL(page.url()).pathname.split('/').pop();
 }
 if(ev.some(e=>e.type==='error'))throw Error(JSON.stringify(ev.filter(e=>e.type==='error')));
 report.chat_events=ev;

 const emittedEntry=replayedEntry||ev.filter(e=>e.type==='code').at(-1)?.files?.find(f=>f.path==='App.tsx');expect(emittedEntry).toBeTruthy();
 await expect.poll(async()=>{const app=await api('GET',`/apps/${appId}`);return app.files?.find(f=>f.path==='App.tsx')?.content===emittedEntry.content},{timeout:30000}).toBe(true);
 const app=await api('GET',`/apps/${appId}`);report.generated_files=app.files;report.appId=appId;
 if(full){fs.writeFileSync(out+'/generated-backend.py',generated.source);for(const file of app.files)if(file.path==='App.tsx')fs.writeFileSync(out+'/generated-App.tsx',file.content);}
 const frame=page.frameLocator('[data-testid="preview-iframe"]');
 async function checkApp(root,label){
  const screenshotPage=root===frame?page:root;
  await expect(root.getByTestId('kpi-transactions')).toHaveText('600',{timeout:45000});
  await expect(root.getByTestId('kpi-exceptions')).toHaveText('100');
  await expect(root.getByTestId('volume-chart').getByTestId('trend-bar')).toHaveCount(15);
  const chartHeights=await root.getByTestId('volume-chart').getByTestId('trend-bar').evaluateAll(bars=>bars.map(b=>(b.matches('rect')?b:b.querySelector('rect,[style*="height"]')||b).getBoundingClientRect().height));
  report.checks[label+'_chart_heights']=chartHeights;
  expect(Math.max(...chartHeights)-Math.min(...chartHeights)).toBeGreaterThan(2);
  await screenshotPage.screenshot({path:out+'/'+label+'-overview.png'});
  await root.getByTestId('nav-transactions').click();await expect(root.getByTestId('transaction-row')).toHaveCount(25);await expect(root.getByTestId('transactions-total')).toHaveText('600');
  await expect(root.getByTestId('transaction-row').first()).toHaveAttribute('data-id','TX-000600');
  await root.getByTestId('page-next').click();await expect(root.getByTestId('current-page')).toHaveText('2');await expect(root.getByTestId('transaction-row').first()).toHaveAttribute('data-id','TX-000575');
  await root.getByTestId('status-filter').selectOption('exception');await root.getByTestId('apply-filters').click();await expect(root.getByTestId('transactions-total')).toHaveText('100');await expect(root.getByTestId('current-page')).toHaveText('1');
  await root.getByTestId('region-filter').selectOption('APAC');await root.getByTestId('apply-filters').click();await expect(root.getByTestId('transactions-total')).toHaveText('0');await expect(root.getByTestId('empty-state')).toBeVisible();
  await root.getByTestId('region-filter').selectOption('all');await root.getByTestId('status-filter').selectOption('all');await root.getByTestId('transaction-search').fill('TX-000600');await root.getByTestId('apply-filters').click();await expect(root.getByTestId('transaction-row')).toHaveCount(1);
  const detailRow=root.getByTestId('transaction-row').first();const detailButton=detailRow.getByRole('button',{name:/detail|view/i});if(await detailButton.count())await detailButton.first().click();else await detailRow.click();await expect(root.getByTestId('transaction-detail')).toContainText('Synthetic Client 040');await expect(root.getByTestId('transaction-detail')).toContainText('Payment received');await expect(root.getByTestId('transaction-detail')).toContainText('Reconciliation mismatch');
  await screenshotPage.screenshot({path:out+'/'+label+'-detail.png'});await root.getByTestId('detail-close').click();
  await root.getByTestId('nav-accounts').click();await expect(root.getByTestId('account-row')).toHaveCount(40);await expect(root.getByTestId('account-row').first()).toContainText('Branch 01');
  await root.getByTestId('nav-exceptions').click();await expect(root.getByTestId('case-row')).toHaveCount(100);await screenshotPage.screenshot({path:out+'/'+label+'-exceptions.png'});
  report.checks[label]={overview:true,chart15days:true,pagination:true,server_filters:true,empty_state:true,search:true,joined_detail_and_audit:true,accounts40:true,cases100:true};
 }
 await checkApp(frame,'preview');
 if(full)await require('./full_banking_checks.cjs').checkWrites({root:frame,page,context,state,credentials,report,out,label:'preview'});
 let published;
 if(full){
  const slug='banking-full-'+Date.now();await page.getByTestId('builder-publish').click();const dialog=page.getByTestId('publish-dialog');await expect(dialog).toBeVisible();await dialog.getByTestId('publish-slug').fill(slug);await dialog.getByTestId('publish-submit').click();await expect(dialog.getByTestId('publish-success')).toBeVisible({timeout:60000});published={slug};report.checks.publish_via_ui=true;
 }else published=await api('POST',`/apps/${appId}/publish`,{slug:'banking-ops-'+Date.now(),access:'public'});
 const pub=await context.newPage();await pub.goto(state.engineUrl+'/a/'+published.slug);await checkApp(pub,'published');
 if(full)await require('./full_banking_checks.cjs').checkWrites({root:pub,page:pub,context,state,credentials,report,out,label:'published'});
 if(process.env.BANKING_DESIGN_PASS==='1'){
  await pub.getByTestId('nav-overview').click();
  await expect(pub.getByTestId('volume-chart').getByTestId('trend-bar')).toHaveCount(15);
  await pub.screenshot({path:out+'/design-overview-light.png'});
  await pub.getByTestId('volume-chart').getByTestId('trend-bar').first().focus();
  await expect(pub.locator('.m-chart-stats')).toContainText('Oct 1');
  await pub.getByTestId('theme-toggle').click();await expect(pub.locator('.meridian')).toHaveAttribute('data-theme','dark');
  await expect(pub.getByTestId('app-refresh')).toHaveCSS('background-color','rgb(26, 38, 46)');
  await expect(pub.locator('.m-health-note button')).toHaveCSS('color','rgb(229, 237, 240)');
  await pub.screenshot({path:out+'/design-overview-dark.png',animations:'disabled'});
  await pub.getByTestId('theme-toggle').click();
  await expect(pub.getByTestId('app-refresh')).toHaveCSS('background-color','rgb(255, 255, 255)');
  await pub.locator('.m-main').evaluate(el=>el.scrollTop=el.scrollHeight);await pub.screenshot({path:out+'/design-portfolio.png'});
  await pub.getByTestId('nav-accounts').click();await expect(pub.getByTestId('account-row')).toHaveCount(40);
  await pub.getByTestId('account-search').fill('Synthetic Client 040');await expect(pub.getByTestId('account-row')).toHaveCount(1);
  await pub.getByTestId('account-search').fill('');await pub.getByTestId('account-sort').click();await expect(pub.getByTestId('account-row').first()).toContainText('Synthetic Client 040');
  await pub.screenshot({path:out+'/design-accounts.png'});
  await pub.getByTestId('nav-exceptions').click();await pub.getByTestId('case-filter-high').click();await expect(pub.getByTestId('case-row')).toHaveCount(50);
  await pub.getByTestId('case-filter-medium').click();await expect(pub.getByTestId('case-row')).toHaveCount(50);
  await pub.getByTestId('case-filter-all').click();await pub.getByTestId('case-search').fill('Reconciliation mismatch');await expect(pub.getByTestId('case-row')).toHaveCount(50);
  await pub.getByTestId('case-search').fill('');await expect(pub.getByTestId('case-row')).toHaveCount(100);await pub.screenshot({path:out+'/design-exceptions.png'});
  await pub.getByTestId('nav-transactions').click();await expect(pub.getByTestId('transaction-row')).toHaveCount(1);
  const downloadPromise=pub.waitForEvent('download');await pub.getByRole('button',{name:'Export page'}).click();
  const download=await downloadPromise;const csv=fs.readFileSync(await download.path(),'utf8');expect(csv).toContain('TX-000600');expect(csv).toContain('account_name');
  await pub.getByRole('button',{name:'View details TX-000600'}).click();await expect(pub.getByRole('dialog')).toBeVisible();
  await pub.keyboard.press('Tab');await expect(pub.getByTestId('detail-close')).toBeFocused();await pub.keyboard.press('Escape');await expect(pub.getByRole('dialog')).toHaveCount(0);
  await expect(pub.getByRole('button',{name:'View details TX-000600'})).toBeFocused();
  await pub.screenshot({path:out+'/design-transactions.png'});
  for(const width of [1024,390]){
   await pub.setViewportSize({width,height:900});await pub.getByTestId('nav-overview').click();
   await expect(pub.getByTestId('volume-chart').getByTestId('trend-bar')).toHaveCount(15);
   const dimensions=await pub.evaluate(()=>{const main=document.querySelector('.m-main');return {outside:Array.from(document.querySelectorAll('*')).filter(el=>!el.closest('.m-main')&&el.getBoundingClientRect().right>innerWidth+1).map(el=>({tag:el.tagName,class:el.className,right:el.getBoundingClientRect().right})),viewport:innerWidth,page:document.documentElement.scrollWidth,main:main.clientWidth,scroll:main.scrollWidth,children:Array.from(main.children).map(el=>({class:el.className,width:el.clientWidth,scroll:el.scrollWidth}))}});
   report.checks['design_dimensions_'+width]=dimensions;
   await pub.screenshot({path:out+'/design-responsive-'+width+'.png'});
   expect(dimensions.page,'page must fit '+width+'px').toBeLessThanOrEqual(dimensions.viewport);
   expect(dimensions.scroll,'main content must fit '+width+'px: '+JSON.stringify(dimensions.children)).toBeLessThanOrEqual(dimensions.main+1);
  }
  await pub.setViewportSize({width:1440,height:900});
  report.checks.design={light_dark:true,chart_keyboard_focus:true,account_search_sort:true,severity_and_case_search:true,csv_export:true,drawer_keyboard:true,responsive_1024_and_390:true};
 }
 // Mutate the actual database and prove summary refresh without changing generated code.
 execFileSync(process.env.DASHBOARD_PYTHON,['-c',`import json,psycopg2
from pathlib import Path
cfg=json.loads(Path(${JSON.stringify(process.env.DASHBOARD_CREDENTIAL_FILE)}).read_text())
with psycopg2.connect(host=${JSON.stringify(state.tmp)},port=cfg['port'],dbname=cfg['secrets']['PG_DATABASE']) as c:
 with c.cursor() as q:q.execute("UPDATE bank_transactions SET status='settled' WHERE id='TX-000600'")
`]);
 await pub.getByTestId('nav-overview').click();await pub.getByTestId('app-refresh').click();await expect(pub.getByTestId('kpi-exceptions')).toHaveText('99');await expect(pub.getByTestId('kpi-transactions')).toHaveText('600');
 report.checks.live_database_refresh=true;
 expect(apiHits.some(h=>new URL(h.url).searchParams.get('page')==='2')).toBe(true);
 expect(apiHits.some(h=>new URL(h.url).searchParams.get('status')==='exception')).toBe(true);
 expect(apiHits.some(h=>new URL(h.url).searchParams.get('region')==='APAC')).toBe(true);
 report.checks.real_server_requests={pagination:true,status_filter:true,region_filter:true};
 function setLogin(enabled){execFileSync(process.env.DASHBOARD_PYTHON,['-c',`import json,psycopg2
from pathlib import Path
from psycopg2 import sql
cfg=json.loads(Path(${JSON.stringify(process.env.DASHBOARD_CREDENTIAL_FILE)}).read_text())
with psycopg2.connect(host=${JSON.stringify(state.tmp)},port=cfg['port'],dbname=cfg['secrets']['PG_DATABASE']) as c:
 with c.cursor() as q:q.execute(sql.SQL('ALTER ROLE {} ${enabled?'LOGIN':'NOLOGIN'}').format(sql.Identifier(cfg['secrets']['PG_USER'])))
`]);}
 setLogin(false);
 try{
  await pub.getByTestId('app-refresh').click();
  await expect(pub.getByRole('button',{name:/retry/i}).first()).toBeVisible({timeout:15000});
 }finally{setLogin(true);}
 const recoveredResponse=pub.waitForResponse(r=>r.url().includes('/ops/summary')&&r.status()===200);
 await pub.getByRole('button',{name:/retry/i}).first().click();
 await recoveredResponse;
 await expect(pub.getByTestId('app-refresh')).toBeEnabled();
 await expect(pub.getByTestId('volume-chart').getByTestId('trend-bar')).toHaveCount(15);
 await expect(pub.getByTestId('kpi-exceptions')).toHaveText('99');
 await expect(pub.getByRole('button',{name:/retry/i})).toHaveCount(0);
 report.checks.database_error_and_retry=true;
 await pub.screenshot({path:out+'/published-refreshed.png'});
 for(const value of privateValues){expect(JSON.stringify(app.files)).not.toContain(value);expect(responses.some(t=>t.includes(value))).toBe(false);}
 expect(report.errors).toEqual([]);report.passed=true;console.log(caseworkExample ? 'PASS: retained Gemini-generated casework app, real Postgres reads/writes, complete UI and publishing' : process.env.BANKING_EXAMPLE==='1' ? 'PASS: redesigned banking app, retained source, real Postgres, interactions and publishing' : 'PASS: complex banking app, real Gemini/Postgres, filters, pagination, joined drilldowns, cases, accounts, publish and live refresh');
})().catch(async e=>{report.passed=false;report.failure=privateValues.reduce((t,v)=>t.split(v).join('[REDACTED]'),e.message);console.log('Banking browser validation failed:',report.failure.slice(0,2000));if(page)await page.screenshot({path:out+'/failure.png'}).catch(()=>{});process.exitCode=1;}).finally(async()=>{fs.writeFileSync(out+'/report.json',privateValues.reduce((t,v)=>t.split(v).join('[REDACTED]'),JSON.stringify(report,null,2)));await browser?.close();});
