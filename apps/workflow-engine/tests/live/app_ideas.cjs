const {chromium,expect}=require(process.env.DASHBOARD_ROOT+'/apps/workflow-studio/node_modules/@playwright/test');
const fs=require('fs');
const state=JSON.parse(fs.readFileSync(process.env.DASHBOARD_STATE_FILE,'utf8'));
const credentials=JSON.parse(fs.readFileSync(process.env.DASHBOARD_CREDENTIAL_FILE,'utf8'));
const privateValues=Object.values(credentials.secrets);
const out=process.env.DASHBOARD_ROOT+'/screenshots/app-ideas';fs.mkdirSync(out,{recursive:true});
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
function db(query,params=[]){
 const code=`import os,json,psycopg2\nfrom psycopg2.extras import RealDictCursor\ncfg=json.load(open(os.environ['DASHBOARD_CREDENTIAL_FILE']))\nargs=json.load(__import__('sys').stdin)\nwith psycopg2.connect(host=${JSON.stringify(state.tmp)},port=cfg['port'],dbname=cfg['secrets']['PG_DATABASE']) as c:\n with c.cursor(cursor_factory=RealDictCursor) as q:\n  q.execute(args['query'],args['params'])\n  print(json.dumps(q.fetchall() if q.description else [],default=str))`;
 return JSON.parse(execFileSync(process.env.DASHBOARD_PYTHON,['-c',code],{input:JSON.stringify({query,params}),encoding:'utf8'}));
}
const cases=[
 {id:'storefront',name:'Small Shop',backend:`Create module shop_api. Existing tables shop_products(sku text PRIMARY KEY,name text,price numeric,stock int CHECK stock>=0) and shop_orders(id text PRIMARY KEY,sku text,quantity int CHECK quantity>0,total numeric).
 Public GET /products name listProducts returns {items:[{sku,name,price,stock}]} ORDER BY sku from real shop_products.
 Public POST /orders name placeOrder accepts req.body {sku:string,quantity:integer}; reject bool/noninteger/nonpositive quantity and missing sku with HTTPError(422,{'error':'invalid_order'}). Generate uuid.uuid4().hex order id. Use ONE atomic PostgreSQL statement: WITH changed AS (UPDATE shop_products SET stock=stock-%s WHERE sku=%s AND stock>=%s RETURNING sku,price) INSERT INTO shop_orders(id,sku,quantity,total) SELECT %s,sku,%s,price*%s FROM changed RETURNING id,sku,quantity,total. Bind quantity,sku,quantity,id,quantity,quantity as parameters. If no rows raise HTTPError(409,{'error':'unavailable'}); otherwise return first row. Do not split stock decrement and order insertion into separate SDK calls. GET does not mutate. Do not create any tables.`,ui:`A polished small storefront with real product cards and stock quantities. Call listProducts on mount. For each product render its stock number only in data-testid={'stock-'+sku}. Show a native select data-testid="product-select" options values product.sku, a numeric input data-testid="quantity-input", and button data-testid="order-submit" labeled Place order. Default sku MUG and quantity 1. On submission call placeOrder with actual selected sku and integer quantity, display returned total as '$'+Number(total).toFixed(2) in data-testid="order-total", then refresh listProducts. No payments, fake checkout, sample data or hardcoded successful orders. Catch errors in visible data-testid="app-error". Render name and price from backend.`,
  schema:{placeOrder:{type:'object',properties:{sku:{type:'string'},quantity:{type:'integer',minimum:1}},required:['sku','quantity']}},
  async backendCheck(test){
   const list=await test('listProducts');expect(list.body.items).toHaveLength(3);
   const order=await test('placeOrder',{body:{sku:'TEST',quantity:1}});expect(order.status).toBe(200);expect(Number(order.body.total)).toBe(7.99);
   for(const quantity of [0,-1,1.5,'bad']){const r=await test('placeOrder',{body:{sku:'TEST',quantity}});expect(r.status).toBe(422);}
   expect((await test('placeOrder',{body:{sku:'TEST',quantity:99}})).status).toBe(409);
   const concurrent=await Promise.all([test('placeOrder',{body:{sku:'TEST',quantity:2}}),test('placeOrder',{body:{sku:'TEST',quantity:2}})]);
   expect(concurrent.map(r=>r.status).sort()).toEqual([200,409]);
   expect(db("SELECT stock FROM shop_products WHERE sku='TEST'")[0].stock).toBe(0);
   expect(db("SELECT sum(quantity)::int AS quantity FROM shop_orders WHERE sku='TEST'")[0].quantity).toBe(3);
   return list.body;
  },
  async browserCheck(root,published){
   await expect(root.getByTestId('stock-MUG')).toHaveText(published?'3':'5');
   await root.getByTestId('product-select').selectOption('MUG');await root.getByTestId('quantity-input').fill(published?'1':'2');await root.getByTestId('order-submit').click();
   await expect(root.getByTestId('order-total')).toHaveText(published?'$12.50':'$25.00');
   await expect(root.getByTestId('stock-MUG')).toHaveText(published?'2':'3');
   expect(db("SELECT stock FROM shop_products WHERE sku='MUG'")[0].stock).toBe(published?2:3);
   expect(Number(db("SELECT sum(total) total FROM shop_orders WHERE sku='MUG'")[0].total)).toBe(published?37.5:25);
  }},
 {id:'support',name:'Support Desk',backend:`Create module support_api. Existing table support_tickets(id text PRIMARY KEY,subject text,status text CHECK status IN ('open','resolved')). Public GET /tickets name listTickets returns {items:[{id,subject,status}]} ORDER BY id, optional req.query status; parameterize WHERE status=%s. Public PATCH /tickets/{id} name resolveTicket accepts req.body {status:'resolved'} only, otherwise HTTPError(422,{'error':'invalid_status'}). UPDATE support_tickets SET status=%s WHERE id=%s RETURNING id,subject,status using req.params['id']; missing row HTTPError(404,{'error':'not_found'}). Return first row. Do not create tables.`,ui:`A polished support operations board. listTickets on mount and after every update. Each ticket shows its subject and status text in data-testid={'ticket-status-'+id}. Each open ticket has a Resolve button data-testid={'resolve-'+id}; click invokes resolveTicket with that ticket id and {status:'resolved'}, then reloads actual server data. Show data-testid="tickets-count" with items.length. Include loading and caught error states. Never optimistically invent a successful result.`,schema:{resolveTicket:{type:'object',properties:{status:{type:'string',enum:['resolved']}},required:['status']}},
  async backendCheck(test){
   const list=await test('listTickets');expect(list.body.items).toHaveLength(3);
   expect((await test('resolveTicket',{params:{id:'T-2'},body:{status:'resolved'}})).status).toBe(200);
   expect((await test('resolveTicket',{params:{id:'T-1'},body:{status:'invalid'}})).status).toBe(422);
   expect((await test('resolveTicket',{params:{id:'MISSING'},body:{status:'resolved'}})).status).toBe(404);
   const filtered=await test('listTickets',{query:{status:'open'}});expect(filtered.body.items.map(x=>x.id)).toEqual(['T-1']);return list.body;
  },
  async browserCheck(root,published){
   await expect(root.getByTestId('tickets-count')).toHaveText('3');
   await expect(root.getByTestId('ticket-status-T-1')).toHaveText(published?'resolved':'open');
   if(!published){await root.getByTestId('resolve-T-1').click();await expect(root.getByTestId('ticket-status-T-1')).toHaveText('resolved');}
   expect(db("SELECT status FROM support_tickets WHERE id='T-1'")[0].status).toBe('resolved');
  }},
 {id:'reporting',name:'Expense Analytics',backend:`Create module expense_api. Existing expenses(id text,department text,category text,amount numeric). Public GET /expenses name listExpenses returns {items:[{id,department,category,amount}],total:number}; optional req.query department filter, parameterized; ORDER BY id. Compute total from matching actual rows. Public GET /expense-summary name expenseSummary returns {items:[{category,total:number}],total:number}; optional same department filter, aggregate SUM(amount) GROUP BY category ORDER BY category. Both derive data from expenses, never stored sample values. Do not create tables.`,ui:`An expense dashboard calling listExpenses and expenseSummary. Native select data-testid="department-filter" with values '' for All, 'Engineering', 'Operations'. Changing filter must fetch both APIs with query department directly as defined by api/client.ts. Show summary total as Number(total).toFixed(2) in data-testid="expense-total"; each expense row has data-testid="expense-row". Show category breakdown from expenseSummary. Refresh button data-testid="app-refresh" reloads both APIs for current filter. Loading, empty and caught error states. Do not hardcode totals, rows or fallback successful values.`,schema:{},
  async backendCheck(test){const list=await test('listExpenses');expect(list.body.items).toHaveLength(5);expect(Number(list.body.total)).toBe(430);const filtered=await test('expenseSummary',{query:{department:'Engineering'}});expect(Number(filtered.body.total)).toBe(230);expect(filtered.body.items.map(x=>Number(x.total))).toEqual([150,80]);return list.body;},
  async browserCheck(root,published){
   await expect(root.getByTestId('expense-total')).toHaveText(published?'500.00':'430.00');
   await expect(root.getByTestId('expense-row')).toHaveCount(published?6:5);
   await root.getByTestId('department-filter').selectOption('Engineering');await expect(root.getByTestId('expense-total')).toHaveText(published?'300.00':'230.00');
   await expect(root.getByTestId('expense-row')).toHaveCount(published?4:3);
   if(!published){db("INSERT INTO expenses VALUES ('E6','Engineering','Software',70)");await root.getByTestId('app-refresh').click();await expect(root.getByTestId('expense-total')).toHaveText('300.00');await expect(root.getByTestId('expense-row')).toHaveCount(4);}
  }},
];
(async()=>{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true});
 const context=await browser.newContext({viewport:{width:1440,height:900}});
 await context.route(/https:\/\/fonts\.(googleapis|gstatic)\.com\//,r=>r.fulfill({status:200,contentType:'text/css',body:''}));
 await context.addInitScript(()=>{window.__liveStreams=[];const orig=window.fetch.bind(window);window.fetch=async(input,init)=>{const r=await orig(input,init);const url=typeof input==='string'?input:input.url||String(input);if(url.includes('/modules/generate')||url.includes('/ai/app-builder/chat'))r.clone().text().then(text=>window.__liveStreams.push({url,text}));return r;};});
 report.ideas={};
 for(const idea of cases){
  console.log('Testing idea:',idea.id);const checks={};report.ideas[idea.id]=checks;page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
  try{
   const p=await api('POST','/projects',{name:idea.name,slug:idea.id+'-'+Date.now()});checks.project=p.id;
   await page.goto(`${state.studioUrl}/p/${p.slug}/secrets`);
   for(const [key,value] of Object.entries(credentials.secrets)){
    await page.getByTestId('secret-add').click();await page.getByTestId('create-secret-key').fill(key);await page.getByTestId('create-secret-value').fill(value);await page.getByTestId('create-secret-hosts-input').fill('127.0.0.1');await page.getByTestId('create-secret-hosts-input').press('Enter');await page.getByTestId('create-secret-save').click();await expect(page.getByTestId(`secret-cell-${key}-dev`)).toHaveAttribute('data-state','set');
   }
   await page.reload();await expect(page.getByTestId('secret-cell-PG_PASSWORD-dev')).toHaveAttribute('data-state','set');checks.saved_secrets=true;
   await page.goto(`${state.studioUrl}/p/${p.slug}/backend/~new`);
   const connection=`Import api, HTTPError from studio; import studio. Use studio.nodes.postgres(connection_string=f"host=127.0.0.1 port=${credentials.port} user={studio.secret('PG_USER')} password={studio.secret('PG_PASSWORD')} dbname={studio.secret('PG_DATABASE')}",operation="query",query=sql,query_parameters=params). Its first result item contains rows. Database credentials are secret REFERENCES only. Every SQL value must use %s binding. External tables exist; no studio.db or fabricated data.`;
   const generated=await generate(connection+' '+idea.backend,idea.id+'-backend');checks.backend={model:generated.model,attempts:generated.attempts,source:generated.source};
   const routes=await api('GET',`/projects/${p.id}/routes`);
   for(const [name,schema] of Object.entries(idea.schema)){const route=routes.find(r=>r.name===name);expect(route).toBeTruthy();await api('PUT',`/projects/${p.id}/routes/${route.id}`,{request_schema:{type:'object',properties:{body:schema},required:['body']},validate_request:true});}
   const test=async(name,params={})=>{const route=routes.find(r=>r.name===name);expect(route).toBeTruthy();const result=await api('POST',`/projects/${p.id}/routes/${route.id}/test`,{environment:'dev',...params});if(result.status>=200&&result.status<300&&result.inferred?.response_schema)await api('PUT',`/projects/${p.id}/routes/${route.id}`,{response_schema:result.inferred.response_schema});return result;};
   checks.backend_baseline=await idea.backendCheck(test);checks.database_assertions=true;
   await page.goto(`${state.studioUrl}/p/${p.slug}?dialog=create-ui`);await page.getByTestId('create-ui-name').fill(idea.name);
   await page.getByTestId('create-ui-prompt').fill(idea.ui+' Use real typed api from ./api/client and read api/client.ts before implementing. Only write App.tsx; React and Tailwind, under 200 lines. No external imports or invented APIs. All state comes from live responses.');
   const before=await streamCount();await page.getByTestId('create-ui-submit').click();const ev=await nextStream(before,'/ai/app-builder/chat');expect(ev.filter(e=>e.type==='error')).toEqual([]);
   const appId=new URL(page.url()).pathname.split('/').pop();const frame=page.frameLocator('[data-testid="preview-iframe"]');await idea.browserCheck(frame,false);checks.preview=true;
   const app=await api('GET',`/apps/${appId}`);checks.generated_files=app.files;await page.screenshot({path:`${out}/${idea.id}-preview.png`});
   const published=await api('POST',`/apps/${appId}/publish`,{slug:idea.id+'-'+Date.now(),access:'public'});const pub=await context.newPage();pub.on('pageerror',e=>errors.push(e.message));await pub.goto(state.engineUrl+'/a/'+published.slug);await idea.browserCheck(pub,true);await pub.screenshot({path:`${out}/${idea.id}-published.png`});checks.published=true;
   for(const value of privateValues){expect(generated.source+JSON.stringify(app.files)).not.toContain(value);expect(await pub.locator('body').innerText()).not.toContain(value);}
   expect(errors).toEqual([]);checks.passed=true;console.log('PASS:',idea.id);await pub.close();
  }catch(error){checks.passed=false;checks.failure=error.message;await page.screenshot({path:`${out}/${idea.id}-failure.png`}).catch(()=>{});console.log('FAIL:',idea.id,privateValues.reduce((text,value)=>text.split(value).join('[REDACTED]'),error.message).slice(0,1200));}
  await page.close();
 }
 report.passed=Object.values(report.ideas).every(idea=>idea.passed);if(!report.passed)process.exitCode=1;
})().catch(e=>{report.passed=false;report.failure=e.message;process.exitCode=1;}).finally(async()=>{fs.writeFileSync(out+'/report.json',privateValues.reduce((text,value)=>text.split(value).join('[REDACTED]'),JSON.stringify(report,null,2)));await browser?.close();});
