// Scenario steps for marketplace.cjs (INTEGRATION-TEST-PLAN section 2). Each step drives the real browser UI and
// then asserts backend state through the API.
exports.run=async function run(E){
 const {state,SV,progress,saveProgress,report,raw,api,pp,P,tablePath,rowCount,allRows,routes,routeByName,testRoute,publicCall,sql,expectStatus,step,S,shot,toast,editorText,setSource,saveEditor,createTableUI,addSecretUI,expect,violations,redact,fs,out}=E;
 const page=()=>E.page, context=()=>E.context;
 const leaks_ref=E.leaks;
 const U=state.upstreamUrl;
 const g=(id)=>page().getByTestId(id);

 // ------------------------------------------------------------------ 01 project
 await step('s01','Create the project in Projects home',async()=>{
  const p=page();
  await p.goto(state.studioUrl+'/projects');
  await g('new-project').click();
  await g('new-project-name').fill('Marketplace Trust & Payments');
  await g('new-project-submit').click();
  await p.waitForURL(/\/p\/[^/]+/);
  const slug=new URL(p.url()).pathname.split('/')[2];
  const proj=(await api('GET','/projects')).find(x=>x.slug===slug);
  expect(proj.name).toBe('Marketplace Trust & Payments');
  expect(proj.environments.map(e=>e.name)).toEqual(['dev','prod']);
  progress.project=proj;saveProgress();
  await expect(g('setup-guide')).toBeVisible();
  await shot('01-project-new');
 });

 // ------------------------------------------------------------------ 02 secrets
 await step('s02','Secrets through the Secrets page (write-only, per environment, host-bound)',async()=>{
  await addSecretUI('PAYMENTS_TOKEN',SV.PAYMENTS_TOKEN,['dev'],['127.0.0.1']);
  await addSecretUI('STRIPE_WEBHOOK_SECRET',SV.STRIPE_WEBHOOK_SECRET,['dev','prod'],[]);
  await addSecretUI('RISK_API_KEY',SV.RISK_API_KEY,['dev','prod'],['127.0.0.1']);
  await page().reload();
  await expect(g('secret-cell-PAYMENTS_TOKEN-dev')).toHaveAttribute('data-state','set');
  const rows=await api('GET',pp('/secrets'));
  const by=(k)=>rows.filter(r=>r.key===k).map(r=>r.environment).sort();
  expect(by('PAYMENTS_TOKEN')).toEqual(['dev']);
  expect(by('STRIPE_WEBHOOK_SECRET')).toEqual(['dev','prod']);
  expect(by('RISK_API_KEY')).toEqual(['dev','prod']);
  expect(rows.every(r=>r.value===null)).toBe(true);
  expect(rows.find(r=>r.key==='PAYMENTS_TOKEN').allowed_hosts).toEqual(['127.0.0.1']);
  expect(rows.find(r=>r.key==='RISK_API_KEY').allowed_hosts).toEqual(['127.0.0.1']);
  expect(privateIn(JSON.stringify(rows))).toBe(false);
  expect(privateIn(await page().evaluate(()=>document.body.innerText))).toBe(false);
  await shot('02-secrets');
 });
 function privateIn(t){return Object.values(SV).some(v=>String(t).includes(v));}

 // ------------------------------------------------------------------ 03 tables
 await step('s03','Create the four tables in the Data view',async()=>{
  await createTableUI('customers',[['name','string'],['email','string'],['country','string'],['tier','string'],['lifetime_value','number']]);
  await createTableUI('refunds',[['customer_id','string'],['reason','string'],['amount','number'],['status','string']]);
  await createTableUI('disputes',[['event_id','string'],['customer_id','string'],['amount','number'],['status','string'],['reason','string'],['opened_at','string'],['closed_reason','string']]);
  await createTableUI('events',[['kind','string'],['ref','string'],['label','string'],['confidence','number'],['detail','string']]);
  const t=await api('GET',pp('/tables'));
  expect(t.map(x=>x.name).sort()).toEqual(['customers','disputes','events','refunds']);
  expect(t.find(x=>x.name==='refunds').columns.map(c=>c.name)).toEqual(['customer_id','reason','amount','status']);
  for(const n of ['customers','refunds','disputes','events'])expect(await rowCount(n)).toBe(0);
  await shot('03-tables');
 });

 // ------------------------------------------------------------------ 04 CSV import
 await step('s04','Import ~200 customers from CSV with column mapping',async()=>{
  const countries=['US','DE','FR','GB','BR','IN','JP','CA'];const tiers=['free','plus','pro','enterprise'];
  const lines=['Full Name,Email Address,Country,Plan,LTV'];
  for(let i=1;i<=200;i++)lines.push(`"Customer ${String(i).padStart(3,'0')}, Esq",cust${i}@example.test,${countries[i%8]},${tiers[i%4]},${(i*37.5)%2000}`);
  await page().goto(`${S()}/data/customers`);
  await g('table-menu').click();await g('table-import').click();
  const sheet=g('import-sheet');await expect(sheet).toBeVisible();
  await sheet.getByTestId('import-file').setInputFiles({name:'customers.csv',mimeType:'text/csv',buffer:Buffer.from(lines.join('\n'))});
  await expect(sheet.getByTestId('import-preview')).toContainText('200 rows');
  await expect(sheet.getByTestId('import-map-row')).toHaveCount(5);
  for(const [i,col] of [[0,'name'],[1,'email'],[2,'country'],[3,'tier'],[4,'lifetime_value']]){
   if(!(await sheet.getByTestId(`import-map-${i}`).innerText()).includes(col)){await sheet.getByTestId(`import-map-${i}`).click();await page().getByTestId(`import-map-${i}-${col}`).click();}
   await expect(sheet.getByTestId(`import-map-${i}`)).toContainText(col);
  }
  await shot('04-import-mapping');
  await sheet.getByTestId('import-submit').click();
  await expect(sheet).toHaveCount(0,{timeout:60000});
  expect(await rowCount('customers')).toBe(200);
  const rows=await allRows('customers');
  const r7=rows.find(r=>r.data.email==='cust7@example.test');
  expect(r7.data).toEqual({name:'Customer 007, Esq',email:'cust7@example.test',country:countries[7%8],tier:tiers[7%4],lifetime_value:7*37.5});
  await expect(g('data-row-count')).toContainText('200 rows');
 });

 // ------------------------------------------------------------------ 05 refunds.py (hand-written from the CRUD template)
 const refundsSource=`from studio import api, HTTPError
import studio

refunds = studio.db.table("refunds")
UPSTREAM = "${U}"
APPROVAL_LIMIT = 500
STATUSES = ("pending", "approved", "rejected")


def _row_id(req):
    try:
        return int(req.params["id"])
    except (KeyError, ValueError):
        raise HTTPError(422, {"error": "invalid_id"})


def _load(req):
    try:
        return refunds.get(_row_id(req))
    except HTTPError:
        raise
    except Exception:
        raise HTTPError(404, {"error": "refund_not_found"})


@api.get("/refunds", name="listRefunds")
def list_refunds(req):
    status = req.query.get("status")
    flt = {"status": status} if status else None
    return refunds.list(filter=flt, limit=int(req.query.get("limit", 100)))


@api.get("/refunds/stats", name="refundStats")
def refund_stats(req):
    balance = studio.nodes.http_request(
        method="GET",
        url=f"{UPSTREAM}/v1/balance",
        headers=[{"name": "Authorization", "value": f"Bearer {studio.secret('PAYMENTS_TOKEN')}"}],
    )
    rows = refunds.list(limit=1000)
    by_status = {}
    total = 0
    for r in rows:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
        total += r["amount"]
    return {"count": len(rows), "total_amount": total, "by_status": by_status, "available": balance[0]["body"]["available"]}


@api.get("/refunds/{id}", name="getRefund")
def get_refund(req):
    return _load(req)


@api.post("/refunds", name="createRefund")
def create_refund(req):
    body = req.body
    if not isinstance(body, dict) or "amount" not in body or "customer_id" not in body:
        raise HTTPError(422, {"error": "customer_id and amount are required"})
    return refunds.insert({
        "customer_id": str(body["customer_id"]),
        "reason": body.get("reason", ""),
        "amount": body["amount"],
        "status": "pending",
    })


@api.put("/refunds/{id}", name="updateRefund")
def update_refund(req):
    row = _load(req)
    body = req.body if isinstance(req.body, dict) else {}
    patch = {k: body[k] for k in ("reason", "amount", "status") if k in body}
    if "status" in patch and patch["status"] not in STATUSES:
        raise HTTPError(422, {"error": "invalid_status"})
    return refunds.update(row["id"], patch)


@api.post("/refunds/{id}/approve", name="approveRefund")
def approve_refund(req):
    row = _load(req)
    if row["amount"] > APPROVAL_LIMIT:
        raise HTTPError(422, {"error": "amount_over_threshold", "limit": APPROVAL_LIMIT, "amount": row["amount"]})
    return refunds.update(row["id"], {"status": "approved"})
`;
 await step('s05','Backend: refunds.py from the CRUD template, edited, saved',async()=>{
  const p=page();
  await p.goto(`${S()}?dialog=new-module`);
  await g('new-module-name').fill('refunds');
  await g('template-crud').click();
  await g('new-module-create').click();
  await expect(g('code-editor')).toBeVisible();
  await expect.poll(editorText).toContain('items = studio.db.table("refunds")');
  await setSource(refundsSource);
  await expect.poll(async()=>(await p.getByTestId('status-errors').count())).toBe(0);
  await saveEditor();
  await toast(/Saved refunds\.py/);
  const mods=await api('GET',pp('/modules'));
  expect(mods.map(m=>m.name)).toEqual(['refunds']);
  const rs=await routes();
  expect(rs.map(r=>`${r.method} ${r.path}`).sort()).toEqual(['GET /refunds','GET /refunds/stats','GET /refunds/{id}','POST /refunds','POST /refunds/{id}/approve','PUT /refunds/{id}'].sort());
  expect(rs.every(r=>r.source==='code')).toBe(true);
  await shot('05-refunds-backend');
 });

 // ------------------------------------------------------------------ 06 refunds data + API tester
 await step('s06','Refunds: try routes in the API tester, create ~60 through routes, approve rule, stats',async()=>{
  const p=page();
  await p.goto(`${S()}/backend/refunds?route=createRefund`);
  await expect(g('test-route-current')).toHaveText('/refunds');
  await g('req-tab-body').click();
  await g('req-body').fill('{"customer_id":"cust1","reason":"damaged in transit","amount":84.5}');
  await g('test-send').click();
  await expect(g('test-response').getByTestId('status-pill')).toHaveAttribute('data-status','200');
  expect(await rowCount('refunds')).toBe(1);
  // the remaining ~59 through the route-test endpoint (same code path as the tester)
  const reasons=['damaged','late','wrong item','duplicate charge','not as described'];
  for(let i=2;i<=60;i++){
   const amount=i%10===0?600+i:5+((i*37)%450);
   const r=await testRoute('createRefund',{body:{customer_id:'cust'+((i*7)%200+1),reason:reasons[i%5],amount}});
   expect(r.status).toBe(200);
  }
  expect(await rowCount('refunds')).toBe(60);
  // approve: under the limit passes, over the limit is a 422
  const rows=await allRows('refunds');
  const ok=rows.find(r=>r.data.amount<=500),big=rows.find(r=>r.data.amount>500);
  const a1=await testRoute('approveRefund',{params:{id:String(ok.id)}});expect(a1.status).toBe(200);expect(a1.body.status).toBe('approved');
  const a2=await testRoute('approveRefund',{params:{id:String(big.id)}});expect(a2.status).toBe(422);expect(a2.body.error).toBe('amount_over_threshold');
  expect((await testRoute('getRefund',{params:{id:'999999'}})).status).toBe(404);
  expect((await testRoute('createRefund',{body:{reason:'x'}})).status).toBe(422);
  expect((await testRoute('updateRefund',{params:{id:String(ok.id)},body:{status:'bogus'}})).status).toBe(422);
  // stats calls the external API with the real secret (proved by the upstream log), response never carries it
  const st=await testRoute('refundStats');
  expect(st.status).toBe(200);expect(st.body.count).toBe(60);expect(st.body.available).toBe(48250.5);expect(st.body.by_status.approved).toBe(1);
  const log=await (await fetch(U+'/__log')).json();
  expect(log.some(l=>l.path==='/v1/balance'&&l.auth_ok)).toBe(true);
  // the UI: send the stats request and see the request-path art
  await p.goto(`${S()}/backend/refunds?route=refundStats`);
  await g('test-send').click();
  await expect(g('test-response').getByTestId('status-pill')).toHaveAttribute('data-status','200');
  await expect(g('request-path-art').getByTestId('request-path-call')).toHaveCount(2);
  const runs=await api('GET',pp('/runs?trigger=route_test&limit=200'));
  expect(runs.length).toBeGreaterThanOrEqual(65);
  await shot('06-refunds-tester');
 });

 // ------------------------------------------------------------------ 07 disputes.py: REAL LLM generate, refine, Keep, Undo, redo
 const DISPUTES_PROMPT=`Ingest Stripe-style dispute webhooks idempotently by event id, store them, list open disputes, and close one with a reason.
Module name: disputes. Use the existing table disputes (columns event_id, customer_id, amount, status, reason, opened_at, closed_reason; all string except amount which is a number).
Routes: POST /disputes/webhook (name ingestDispute) accepts {"id": "evt_...", "type": "charge.dispute.created", "data": {"object": {"customer": "cust1", "amount": 1250, "reason": "fraudulent"}}} and stores one row per event id with status "open" and opened_at set to an ISO timestamp; delivering the same event id again must not create a second row and returns the existing row with status 200; a body without id returns 422.
GET /disputes (name listDisputes) lists open disputes (optional ?status= filter).
POST /disputes/{id}/close (name closeDispute) takes {"reason": "..."} (422 when missing), sets status "closed" and closed_reason, 404 for an unknown id.`;
 const REFINE_BASE=`Add GET /disputes/summary (name disputeSummary) returning {"counts": {"open": n, "closed": n}, "total_amount": number} - counts of disputes by status and the total disputed amount (sum of amount over all rows). Keep the existing routes unchanged.`;
 const REFINE_FIX=` Also fix POST /disputes/{id}/close: route handlers take exactly one argument, req, and path parameters come from req.params["id"] (not from a function argument, not req.path_params), the request body from req.body.`;
 if(process.env.MARKETPLACE_DISPUTES_SOURCE)await step('s07','Backend: disputes.py (RETAINED real-Gemini source from a previous full run, no model calls)',async()=>{
  const src=fs.readFileSync(process.env.MARKETPLACE_DISPUTES_SOURCE,'utf8');
  await api('POST',pp('/modules'),{name:'disputes',source:src});
  report.disputes_source_reused=true;report.notes.push('disputes.py reused from the full run (generated + refined by real Gemini there)');
  expect((await routes()).map(r=>r.name)).toEqual(expect.arrayContaining(['ingestDispute','listDisputes','closeDispute','disputeSummary']));
 }); else await step('s07','Backend: disputes.py generated by the real LLM, refined, Keep / Undo / redo',async(attempt)=>{
  const p=page();
  const seen=new Set();
  if(!progress.disputesGenerated){
   await p.goto(`${S()}/backend/~new`);
   await expect(g('composer')).toBeVisible();
   await p.evaluate(()=>{window.__seen=new Set();const scan=()=>{document.querySelectorAll('[data-testid^="phase-"],[data-testid="composer-streaming"],[data-testid="composer-say"]').forEach(el=>window.__seen.add(el.getAttribute('data-testid')))};new MutationObserver(scan).observe(document.body,{subtree:true,childList:true,attributes:true});});
   const ev=await E.generateUI(DISPUTES_PROMPT);
   const result=ev.find(e=>e.type==='result');
   report.disputes_generate={model:result.model,attempts:result.attempts,self_test:result.self_test,module:result.module_name,phases:ev.filter(e=>e.type==='phase'||e.type==='status').map(e=>e.phase||e.status).slice(0,20)};
   const states=await p.evaluate(()=>[...window.__seen]);
   expect(states).toEqual(expect.arrayContaining(['composer-streaming','phase-generating']));
   await expect(g('composer-result')).toBeVisible();
   await expect(g('composer-say')).not.toBeEmpty();
   await shot('07a-disputes-generated');
   const src=await editorText();
   expect(src).toMatch(/studio\.db\.table\(\s*["']disputes["']\s*\)/);
   expect(privateIn(src)).toBe(false);
   await g('save-module').click();
   await toast(/Saved disputes\.py/);
   progress.disputesGenerated=true;saveProgress();
  }
  const rs=await routes();
  const names=rs.map(r=>r.name);
  for(const n of ['ingestDispute','listDisputes','closeDispute'])expect(names).toContain(n);
  expect((await api('GET',pp('/modules'))).map(m=>m.name)).toContain('disputes');
  // behaviour probe of the generated code (a user would try the route before moving on)
  let REFINE_PROMPT=REFINE_BASE;
  {
   const probeEvt={id:'evt_probe',type:'charge.dispute.created',data:{object:{customer:'cust0',amount:1,reason:'probe'}}};
   const ing=await testRoute('ingestDispute',{body:probeEvt});expect(ing.status).toBe(200);
   const probe=await testRoute('closeDispute',{params:{id:String(ing.body.id)},body:{reason:'probe'}});
   const rows=await allRows('disputes');
   for(const r of rows.filter(r=>r.data.event_id==='evt_probe'))await api('DELETE',(await tablePath('disputes'))+'/rows/'+r.id);
   if(probe.status!==200){report.llm_defects=(report.llm_defects||[]);report.llm_defects.push({step:'s07',route:'closeDispute',symptom:`HTTP ${probe.status} ${JSON.stringify(probe.body).slice(0,120)}`,cause:'generated handler took (req, id=None) / req.path_params instead of req.params',fixed_by:'user-style refinement prompt'});REFINE_PROMPT=REFINE_BASE+REFINE_FIX;}
  }
  // refine: diff, Keep, Undo once, then redo
  await p.goto(`${S()}/backend/disputes`);
  await expect(g('code-editor').locator('.cm-content')).toBeVisible();
  const before=await editorText();
  const savedSha=(await api('GET',pp('/modules'))).find(m=>m.name==='disputes').source_sha;
  let ev1=await E.generateUI(REFINE_PROMPT);
  expect(ev1.find(e=>e.type==='result').source).toContain('disputeSummary');
  await expect(g('composer-result')).toBeVisible();
  const diff=g('composer-diff-view');await expect(diff).toBeVisible();
  await expect(diff.locator('[data-diff="add"]').first()).toBeVisible();
  await shot('07b-disputes-refine-diff');
  await g('composer-diff-view-apply').click();
  await expect(g('composer-applied')).toBeVisible();
  await expect(g('composer-flow-head')).toContainText('Kept');
  await g('composer-applied-undo').click();
  // the editor virtualises long documents: compare the head, and use the outline for the draft route
  await expect.poll(async()=>(await editorText()).split('\n').slice(0,15).join('\n')).toBe(before.split('\n').slice(0,15).join('\n'));
  await expect(g('outline-new-route')).toHaveCount(0);
  await expect(g('composer-result')).toHaveCount(0);
  await expect(g('composer-applied')).toHaveCount(0);
  expect((await api('GET',pp('/modules'))).find(m=>m.name==='disputes').source_sha).toBe(savedSha);
  report.notes.push('disputes: Keep then Undo restored the previous source; redo re-ran the refinement (one more real call)');
  const ev2=await E.generateUI(REFINE_PROMPT);
  const refined=ev2.find(e=>e.type==='result').source;
  expect(refined).toContain('disputeSummary');expect(privateIn(refined)).toBe(false);
  await expect(g('composer-result')).toBeVisible();
  await expect(g('composer-diff-view')).toBeVisible();
  await g('composer-diff-view-apply').click();
  await expect(g('composer-applied')).toBeVisible();
  await saveEditor();
  await toast(/Saved disputes\.py/);
  expect((await routes()).map(r=>r.name)).toContain('disputeSummary');
  await shot('07c-disputes-saved');
  report.disputes_source_bytes=refined.length;fs.writeFileSync(out+'/generated-disputes.py',refined);
 },{llm:true});

 // dispute behaviour through the API (independent expectations)
 await step('s07b','Disputes behaviour: idempotent ingest, list, close, summary',async()=>{
  const ev=(id,amount,customer)=>({id,type:'charge.dispute.created',data:{object:{customer,amount,reason:'fraudulent'}}});
  const dRows=async()=>(await allRows('disputes')).map(r=>({id:r.id,...r.data}));
  for(let i=1;i<=6;i++){const r=await testRoute('ingestDispute',{body:ev('evt_'+i,100*i,'cust'+i)});expect(r.status,JSON.stringify(r.body)).toBe(200);}
  expect(await rowCount('disputes')).toBe(6);
  const dup=await testRoute('ingestDispute',{body:ev('evt_3',300,'cust3')});expect(dup.status).toBe(200);
  expect(await rowCount('disputes')).toBe(6);
  expect((await dRows()).filter(r=>r.event_id==='evt_3')).toHaveLength(1);
  expect((await testRoute('ingestDispute',{body:{type:'x'}})).status).toBe(422);
  const open=await testRoute('listDisputes');expect(open.status).toBe(200);
  const openList=Array.isArray(open.body)?open.body:(open.body.items||open.body.disputes||[]);
  expect(openList).toHaveLength(6);expect(openList.every(d=>d.status==='open')).toBe(true);
  const rows=await dRows();const target=rows.find(r=>r.event_id==='evt_2');
  expect((await testRoute('closeDispute',{params:{id:String(target.id)},body:{}})).status).toBe(422);
  const closed=await testRoute('closeDispute',{params:{id:String(target.id)},body:{reason:'won: evidence submitted'}});
  expect(closed.status).toBe(200);
  expect((await testRoute('closeDispute',{params:{id:'999999'},body:{reason:'x'}})).status).toBe(404);
  const after=(await dRows()).find(r=>r.id===target.id);
  expect(after.status).toBe('closed');expect(after.closed_reason).toBe('won: evidence submitted');
  const again=await testRoute('listDisputes');const l2=Array.isArray(again.body)?again.body:(again.body.items||again.body.disputes||[]);
  expect(l2).toHaveLength(5);
  const sum=await testRoute('disputeSummary');expect(sum.status).toBe(200);
  const all=await dRows();
  expect(sum.body.counts.open).toBe(all.filter(r=>r.status==='open').length);
  expect(sum.body.counts.closed).toBe(all.filter(r=>r.status==='closed').length);
  expect(sum.body.total_amount).toBe(all.reduce((n,r)=>n+Number(r.amount),0));
 });

 // ------------------------------------------------------------------ 08 customers.py (egress allowlist)
 const BLOCKED=U.replace('127.0.0.1','localhost');
 const customersSource=`from studio import api, HTTPError
import studio

customers = studio.db.table("customers")
RISK_URL = "${U}"
OTHER_URL = "${BLOCKED}"


def _find(req):
    cid = req.query.get("id") or req.params.get("id")
    email = req.query.get("email")
    if cid:
        try:
            return customers.get(int(cid))
        except Exception:
            raise HTTPError(404, {"error": "customer_not_found"})
    if email:
        rows = customers.list(filter={"email": email}, limit=1)
        if rows:
            return rows[0]
    raise HTTPError(404, {"error": "customer_not_found"})


def _score(base, row):
    res = studio.nodes.http_request(
        method="GET",
        url=f"{base}/v1/risk?customer={row['email']}",
        headers=[{"name": "X-Api-Key", "value": studio.secret("RISK_API_KEY")}],
    )
    return res[0]["body"]["score"]


@api.get("/customers/lookup", name="lookupCustomer")
def lookup_customer(req):
    return _find(req)


@api.get("/customers/{id}/risk", name="customerRisk")
def customer_risk(req):
    row = _find(req)
    score = _score(RISK_URL, row)
    return {"customer_id": row["id"], "email": row["email"], "score": score, "level": "high" if score >= 70 else "normal"}


@api.get("/customers/{id}/risk-secondary", name="customerRiskSecondary")
def customer_risk_secondary(req):
    row = _find(req)
    return {"customer_id": row["id"], "score": _score(OTHER_URL, row)}
`;
 await step('s08','Backend: customers.py with an allowed and a blocked egress host',async()=>{
  const p=page();
  await p.goto(`${S()}?dialog=new-module`);
  await g('new-module-name').fill('customers');
  await g('template-crud').click();await g('new-module-create').click();
  await expect(g('code-editor')).toBeVisible();
  await setSource(customersSource);
  await saveEditor();await toast(/Saved customers\.py/);
  const rs=await routes();
  for(const n of ['lookupCustomer','customerRisk','customerRiskSecondary'])expect(rs.map(r=>r.name)).toContain(n);
  const rows=await allRows('customers');const c=rows.find(r=>r.data.email==='cust9@example.test');
  const l=await testRoute('lookupCustomer',{query:{email:'cust9@example.test'}});expect(l.status).toBe(200);expect(l.body.email).toBe('cust9@example.test');
  const l2=await testRoute('lookupCustomer',{query:{id:String(c.id)}});expect(l2.status).toBe(200);expect(l2.body.id).toBe(c.id);
  expect((await testRoute('lookupCustomer',{query:{email:'nobody@example.test'}})).status).toBe(404);
  // allowed host: real score from the upstream, which saw the real key
  const ok=await testRoute('customerRisk',{params:{id:String(c.id)}});
  expect(ok.status,JSON.stringify(ok.body)).toBe(200);
  const email='cust9@example.test';expect(ok.body.score).toBe(([...email].reduce((n,ch)=>n+ch.charCodeAt(0),0)*7)%100);
  const log=await (await fetch(U+'/__log')).json();
  expect(log.some(x=>x.path.startsWith('/v1/risk')&&x.risk_key_ok)).toBe(true);
  // blocked host: readable error, failed run with a stack trace, upstream never reached on that host
  const blockedBefore=log.filter(x=>String(x.host).startsWith('localhost')).length;
  await p.goto(`${S()}/backend/customers?route=customerRiskSecondary`);
  await g('req-tab-params').click();
  await g('param-id').fill(String(c.id));
  await g('test-send').click();
  await expect(g('test-response').getByTestId('status-pill')).toHaveAttribute('data-status','502');
  await expect(g('run-error-message')).toBeVisible();
  const msg=await g('run-error-message').innerText();report.egress_blocked_message=msg.slice(0,300);
  expect(msg).toMatch(/host|allow|not allowed|bound/i);
  expect(privateIn(msg)).toBe(false);
  await shot('08-customers-egress-blocked');
  const bad=await testRoute('customerRiskSecondary',{params:{id:String(c.id)}});
  expect(bad.status).toBe(502);
  const log2=await (await fetch(U+'/__log')).json();
  expect(log2.filter(x=>String(x.host).startsWith('localhost')).length).toBe(blockedBefore);
  const failedRuns=(await api('GET',pp('/runs?status=failed&limit=20')));
  expect(failedRuns.length).toBeGreaterThan(0);
  const detail=await api('GET',pp('/runs/'+failedRuns[0].id));
  expect(JSON.stringify(detail)).not.toMatch(/pay_|whsec_|risk_[0-9a-f]{20}/);
  expect(detail.metadata?.error?.traceback||detail.metadata?.error?.stack||JSON.stringify(detail.metadata?.error||{})).toBeTruthy();
  report.egress_failed_run=detail.id;
 });

 // ------------------------------------------------------------------ 09 workflow "Refund triage" (real Gemini classify node)
 const triageWorkflow=()=>({
  name:'Refund triage',
  nodes:[
   {name:'Webhook',type:'Webhook',parameters:{method:'POST',path:'refund-triage',responseMode:'onReceived'},position:{x:0,y:120}},
   {name:'Classify',type:'LLMChat',parameters:{model:state.model,temperature:0,maxTokens:1024,
    systemPrompt:'You triage customer messages for a marketplace. Reply with ONLY a compact JSON object {"label": "refund" | "dispute" | "other", "confidence": <number between 0 and 1>}. No prose, no code fences.',
    userMessage:'{{ $json.body.message }}'},position:{x:260,y:120}},
   {name:'Parse',type:'Code',parameters:{code:`import json, re
txt = json_data.get("response", "")
m = re.search(r"\\{.*\\}", txt, re.S)
d = json.loads(m.group(0)) if m else {}
hook = node_data["Webhook"]["json"]["body"]
return [new_item({
    "label": str(d.get("label", "other")).lower(),
    "confidence": float(d.get("confidence", 0)),
    "message": hook["message"],
    "customer_id": hook["customer_id"],
    "amount": hook["amount"],
})]`},position:{x:520,y:120}},
   {name:'Is refund',type:'If',parameters:{condition:'{{ $json.label == "refund" and $json.confidence >= 0.6 }}'},position:{x:780,y:120}},
   {name:'Create refund',type:'HttpRequest',parameters:{method:'POST',url:`${state.engineUrl}/api/p/${P().slug}/refunds`,
    headers:[{name:'Content-Type',value:'application/json'}],
    body:'{"customer_id": "{{ $json.customer_id }}", "reason": "triage: {{ $json.message }}", "amount": {{ $json.amount }}}'},position:{x:1040,y:40}},
   {name:'Log refund',type:'DataTable',parameters:{table:'events',operation:'insert',
    data:'{"kind": "refund_created", "ref": "{{ $json.body.id }}", "label": "refund", "confidence": {{ $node["Parse"].json.confidence }}, "detail": "created via triage"}'},position:{x:1300,y:40}},
   {name:'Needs review',type:'DataTable',parameters:{table:'events',operation:'insert',
    data:'{"kind": "needs_review", "ref": "{{ $json.customer_id }}", "label": "{{ $json.label }}", "confidence": {{ $json.confidence }}, "detail": "left for a human"}'},position:{x:1040,y:220}},
  ],
  connections:[
   {source_node:'Webhook',source_output:'main',target_node:'Classify',target_input:'main'},
   {source_node:'Classify',source_output:'main',target_node:'Parse',target_input:'main'},
   {source_node:'Parse',source_output:'main',target_node:'Is refund',target_input:'main'},
   {source_node:'Is refund',source_output:'true',target_node:'Create refund',target_input:'main'},
   {source_node:'Create refund',source_output:'main',target_node:'Log refund',target_input:'main'},
   {source_node:'Is refund',source_output:'false',target_node:'Needs review',target_input:'main'},
  ]});

 const evRows=async()=>(await allRows('events')).map(r=>({id:r.id,...r.data}));
 const wfRuns=async(id)=>api('GET',pp(`/runs?workflow_id=${id}&limit=100`));
 const input=(body)=>JSON.stringify({body});
 const REFUND_MSG={message:'I was charged twice for the same order and I want my money back, please refund me.',customer_id:'cust7',amount:42};
 await step('s09','Workflow: import "Refund triage" in the UI, test run from the editor (real Gemini classify)',async()=>{
  const p=page();
  const file=state.tmp+'/refund-triage.json';fs.writeFileSync(file,JSON.stringify(triageWorkflow()));
  await p.goto(`${S()}/workflows`);
  await g('workflows-import-file').setInputFiles(file);
  await toast(/Imported Refund triage/);
  const wfs=await api('GET',pp('/workflows'));
  const wf=wfs.find(w=>w.name==='Refund triage');expect(wf).toBeTruthy();expect(wf.node_count).toBe(7);
  progress.workflowId=wf.id;saveProgress();
  await g('workflow-open-'+wf.id).click();
  await expect(p.getByText('Classify').first()).toBeVisible();
  await shot('09a-editor-idle');
  // node details
  await p.locator('.react-flow__node',{hasText:'Classify'}).first().dblclick({position:{x:28,y:28}});
  await expect(g('ndv-modal')).toBeVisible();
  await shot('09b-node-details');
  await g('ndv-close').click();
  // test run with a real payload
  const refundsBefore=await rowCount('refunds'),eventsBefore=await rowCount('events');
  await g('editor-toggle-logs').click();
  await p.getByText('Input',{exact:true}).first().click();
  const cm=p.locator('.cm-content').last();await cm.click();await p.keyboard.press('ControlOrMeta+a');await p.keyboard.press('Delete');await p.keyboard.insertText(JSON.stringify(REFUND_MSG));
  await g('editor-run-with-payload').click();
  await p.getByText('Logs',{exact:true}).first().click();
  await expect(g('execution-status')).toContainText(/success/i,{timeout:240000});
  await shot('09c-editor-running-result');
  await expect.poll(async()=>(await wfRuns(wf.id)).map(r=>r.status)).toEqual(['success']);
  const run=(await wfRuns(wf.id))[0];
  const detail=await api('GET',pp('/runs/'+run.id));
  expect(run.trigger.type).toBe('manual');
  expect(privateIn(JSON.stringify(detail))).toBe(false);
  // backend state: classification took the refund branch (or, if the model hedged, the review branch) and wrote exactly one event
  const ev=await evRows();expect(ev.length).toBe(eventsBefore+1);
  const last=ev[0];report.triage_test_run={event:last,label_branch:last.kind};
  expect(['refund_created','needs_review']).toContain(last.kind);
  if(last.kind==='refund_created'){expect(await rowCount('refunds')).toBe(refundsBefore+1);}
  else report.notes.push('triage test run took needs_review (model confidence below 0.6 or label mismatch): '+JSON.stringify(last));
  expect(last.confidence).toBeGreaterThan(0);
 },{llm:true});

 await step('s10','Workflow: publish, webhook via HTTP, failing input, schedule',async()=>{
  const p=page();const wfId=progress.workflowId;
  await p.goto(`${S()}/workflows/${wfId}`);
  await expect(g('editor-publish')).toBeEnabled({timeout:30000});await g('editor-publish').click();
  await expect(g('editor-unpublish')).toBeVisible({timeout:30000});
  await expect.poll(async()=>(await api('GET','/workflows/'+wfId)).active).toBe(true);
  await shot('10a-published');
  const hook=(body)=>raw('POST','/webhook/p/refund-triage',body);
  const refundsBefore=await rowCount('refunds');const idsBefore=new Set((await evRows()).map(e=>e.id)),evBefore=idsBefore.size;
  const r1=await hook({message:'This item arrived broken, I need a refund for the whole amount.',customer_id:'cust11',amount:77});
  expect(r1.status,r1.text.slice(0,300)).toBe(200);
  const r2=await hook({message:'What are your opening hours on Sunday?',customer_id:'cust12',amount:0});
  expect(r2.status).toBe(200);
  await expect.poll(async()=>(await evRows()).length,{timeout:240000}).toBe(evBefore+2);
  const fresh=(await evRows()).filter(e=>!idsBefore.has(e.id));
  report.triage_webhook_outcomes=fresh.map(e=>e.kind+':'+e.label+':'+e.confidence);
  const note=fresh.find(e=>e.ref==='cust12');expect(note.kind).toBe('needs_review');
  const made=fresh.find(e=>e.ref!=='cust12');
  if(made.kind==='refund_created')expect(await rowCount('refunds')).toBe(refundsBefore+1);
  // deliberately failing input: no message -> a failed run with a traceback, no LLM call
  const l0=E.llmCount();
  const failedBefore=(await wfRuns(wfId)).filter(r=>r.status==='failed').length;
  const bad=await hook({customer_id:'cust13'});
  await expect.poll(async()=>(await wfRuns(wfId)).filter(r=>r.status==='failed').length,{timeout:60000}).toBe(failedBefore+1);
  expect(E.llmCount()).toBe(l0);
  const failed=(await wfRuns(wfId)).filter(r=>r.status==='failed')[0];
  const fd=await api('GET',pp('/runs/'+failed.id));
  report.triage_failed_run={id:failed.id,error:JSON.stringify(fd.metadata?.error||fd.error||{}).slice(0,300)};
  expect(JSON.stringify(fd)).toMatch(/message|required|Traceback|error/i);
  await p.goto(`${S()}/runs/${failed.id}`);
  await expect(g('run-sheet')).toBeVisible();await shot('10b-failed-run-sheet');
  // schedule: a Cron workflow writes an audit event every few seconds once published
  const cron={name:'Scheduled audit',nodes:[{name:'Every 10s',type:'Cron',parameters:{mode:'interval',interval:10},position:{x:0,y:0}},
   {name:'Audit',type:'DataTable',parameters:{table:'events',operation:'insert',data:'{"kind": "scheduled_audit", "ref": "cron", "label": "tick", "confidence": 1, "detail": "{{ $json.schedule }}"}'},position:{x:260,y:0}}],
   connections:[{source_node:'Every 10s',source_output:'main',target_node:'Audit',target_input:'main'}]};
  const cf=state.tmp+'/cron.json';fs.writeFileSync(cf,JSON.stringify(cron));
  await p.goto(`${S()}/workflows`);await g('workflows-import-file').setInputFiles(cf);await toast(/Imported Scheduled audit/);
  const cw=(await api('GET',pp('/workflows'))).find(w=>w.name==='Scheduled audit');
  await g('workflow-open-'+cw.id).click();await expect(g('editor-publish')).toBeEnabled({timeout:30000});await g('editor-publish').click();
  await expect(g('editor-unpublish')).toBeVisible({timeout:30000});
  await expect.poll(async()=>(await api('GET','/workflows/'+cw.id)).active).toBe(true);
  await expect.poll(async()=>(await wfRuns(cw.id)).filter(r=>r.trigger.type==='cron'&&r.status==='success').length,{timeout:90000}).toBeGreaterThanOrEqual(1);
  await g('editor-unpublish').click();
  await expect.poll(async()=>(await api('GET','/workflows/'+cw.id)).active).toBe(false);
  expect((await evRows()).some(e=>e.kind==='scheduled_audit')).toBe(true);
  report.schedule={workflow:cw.id,cron_runs:(await wfRuns(cw.id)).length};
 },{llm:true});

 // ------------------------------------------------------------------ 11 apps built with the real LLM in the app builder
 const COMMON=`Plain React + Tailwind classes, inline SVG/CSS only, no external UI or chart libraries. Only write App.tsx; import { api } from ./api/client and read the exact typed method signatures and response shapes there. Real data only: no hardcoded rows or numbers. Show loading, error and empty states. Keep data-testid attributes exactly as named. Never call console.error or console.warn: report failures only in the UI. Keep the code under 400 lines.`;
 async function appTurn(prompt,{create=null,reuse=null}={}){
  const p=page();
  // diagnostic only (MARKETPLACE_REUSE=1): re-verify an app generated earlier on this stack without new model calls
  if(process.env.MARKETPLACE_REUSE==='1'&&(reuse||progress.currentApp)){const id=reuse||progress.currentApp;progress.currentApp=id;await p.goto(`${S()}/apps/${id}`);await expect(g('preview-iframe')).toBeVisible();report.notes.push('REUSED app '+id+' (no model calls)');return {appId:id,ev:[],source:''};}
  let before=await E.streamCount();
  if(create){
   await p.goto(`${S()}?dialog=create-ui`);
   before=await E.streamCount();            // a full navigation resets window.__liveStreams
   const dlg=g('create-ui-dialog');await expect(dlg).toBeVisible();
   // route picker: tick exactly the routes this app uses
   for(const name of ['listRefunds','getRefund','createRefund','updateRefund','approveRefund','refundStats','ingestDispute','listDisputes','closeDispute','disputeSummary','lookupCustomer','customerRisk','customerRiskSecondary']){
    const box=dlg.getByTestId(`create-ui-route-check-${name}`);if(!await box.count())continue;
    const checked=(await box.getAttribute('data-checked'))!==null;if(checked!==create.routes.includes(name))await box.click();
   }
   await dlg.getByTestId('create-ui-name').fill(create.name);
   await dlg.getByTestId('create-ui-prompt').fill(prompt);
   await dlg.getByTestId('create-ui-submit').click();
  }else{
   // a toast (e.g. "You're set up") sits over the Send button: dismiss it the way a user would
   for(let i=0;i<5&&await p.locator('[data-sonner-toast]').count();i++){const x=p.locator('[data-sonner-toast] [data-close-button]').first();if(await x.count())await x.click({timeout:3000}).catch(()=>{});await p.waitForTimeout(500);}
   await g('chat-input').fill(prompt);await g('chat-send').click();
  }
  const ev=await E.nextStream(before,'/ai/app-builder/chat');
  if(ev.some(e=>e.type==='error'))throw Error('app builder error: '+JSON.stringify(ev.filter(e=>e.type==='error')).slice(0,400));
  const appId=new URL(p.url()).pathname.split('/').pop();
  const entry=ev.filter(e=>e.type==='code').at(-1)?.files?.find(f=>f.path==='App.tsx');
  if(!entry)throw Error('builder produced no App.tsx: '+JSON.stringify(ev.slice(-3)).slice(0,400));
  await expect.poll(async()=>(await api('GET','/apps/'+appId)).files?.find(f=>f.path==='App.tsx')?.content===entry.content,{timeout:60000}).toBe(true);
  return {appId,ev,source:entry.content};
 }
 const p0=()=>page();
 const frame=()=>page().frameLocator('[data-testid="preview-iframe"]');
 const assertNoSecretsInApp=async(appId)=>{const app=await api('GET','/apps/'+appId);expect(privateIn(JSON.stringify(app.files))).toBe(false);return app;};

 await step('s11a','App: "Refunds console" built in the app builder (create + 2 chat refinements)',async()=>{
  for(const n of ['listRefunds','getRefund','approveRefund','refundStats'])await testRoute(n,{params:{id:String((await allRows('refunds'))[0].id)},apply:true});
  const create=`Build a compact "Refunds console" for a marketplace trust team. Header with the title. A row of three stat tiles from refundStats: data-testid tile-count (ONLY the raw refund count), tile-total (total_amount formatted USD), tile-available (available balance formatted USD). Below, a table of refunds from listRefunds: one data-testid refund-row per refund with data-id set to the refund id; columns customer, reason, amount (USD), status. A Refresh button data-testid app-refresh re-fetches stats and refunds. ${COMMON}`;
  const t1=await appTurn(create,{reuse:progress.refundsApp,create:{name:'Refunds console',routes:['listRefunds','getRefund','approveRefund','refundStats']}});
  progress.refundsApp=t1.appId;saveProgress();
  await expect(frame().getByTestId('tile-count')).toHaveText(String(await rowCount('refunds')),{timeout:60000});
  await expect(frame().getByTestId('refund-row')).toHaveCount(await rowCount('refunds'));
  await shot('11a-refunds-turn1');
  const refine1=`Add a status filter: native select data-testid status-filter with options all, pending, approved, rejected; changing it must call listRefunds with the status query (server-side, "all" sends none). Add an Approve button data-testid approve-<id> (the refund id) on each pending row that calls approveRefund with the id, then refreshes the table and stats; when the call fails show the backend error text in an element data-testid approve-error, never pretend it worked. ${COMMON}`;
  const t2=await appTurn(refine1);
  const pend=(await allRows('refunds')).filter(r=>r.data.status==='pending');
  await frame().getByTestId('status-filter').selectOption('pending');
  await expect(frame().getByTestId('refund-row')).toHaveCount(pend.length,{timeout:30000});
  const small=pend.find(r=>r.data.amount<=500),big=pend.find(r=>r.data.amount>500);
  await frame().getByTestId('approve-'+small.id).click();
  await expect.poll(async()=>(await api('GET',(await tablePath('refunds'))+'/rows/'+small.id)).data.status,{timeout:30000}).toBe('approved');
  await expectStatus([{method:'POST',url:/\/refunds\/\d+\/approve$/,status:422}],async()=>{
   await frame().getByTestId('approve-'+big.id).click();
   await expect(frame().getByTestId('approve-error')).toBeVisible({timeout:30000});
  });
  expect((await api('GET',(await tablePath('refunds'))+'/rows/'+big.id)).data.status).toBe('pending');
  await shot('11a-refunds-turn2');
  const refine2=`Polish: show each status as a small coloured badge (data-testid status-badge on each row), right-align amounts, keep all earlier data-testids and behaviour, add a subtitle line showing how many rows are listed (data-testid rows-shown, ONLY the number). ${COMMON}`;
  await appTurn(refine2);
  await expect(frame().getByTestId('status-badge').first()).toBeVisible({timeout:30000});
  const shown=await frame().getByTestId('refund-row').count();
  await expect(frame().getByTestId('rows-shown')).toHaveText(String(shown));
  await shot('11a-refunds-turn3');
  const app=await assertNoSecretsInApp(t1.appId);
  const usage=await api('GET',`/apps/${t1.appId}/api-usage`);
  expect(usage.map(u=>u.operation)).toEqual(expect.arrayContaining(['listRefunds','refundStats','approveRefund']));
  expect(usage.every(u=>u.status!=='missing')).toBe(true);
  const versions=await api('GET',`/apps/${t1.appId}/versions`).catch(()=>[]);
  report.refunds_app={files:app.files.map(f=>f.path),versions:Array.isArray(versions)?versions.length:null};
  // version history UI
  await g('builder-history').click();await expect(g('version-history')).toBeVisible();await shot('11a-version-history');await g('builder-history').click();
  // API tester: send a request at the live route
  await g('builder-settings').click();await g('app-settings-open-tester').click();await expect(g('builder-api-tester')).toBeVisible();
  await p0().getByPlaceholder('https://api.example.com/resource').fill(`${U}/__log`);
  await g('api-tester-send').click();
  await expect(g('api-tester-status')).toBeVisible({timeout:30000});
  report.api_tester_status=(await g('api-tester-status').innerText()).slice(0,120);
  await shot('11a-api-tester');await g('api-tester-close').click();
 },{llm:true});

 await step('s11b','App: "Disputes board" built in the app builder (create + chat refinement)',async()=>{
  for(const n of ['listDisputes','disputeSummary'])await testRoute(n,{apply:true});
  const d=(await allRows('disputes'))[0];
  const create=`Build a "Disputes board" for a marketplace trust team. Stat tiles from disputeSummary: data-testid tile-open (ONLY the raw open count), tile-closed (ONLY the raw closed count), tile-amount (total disputed amount formatted USD). Below, the list of OPEN disputes from listDisputes (no status query): one data-testid dispute-row per dispute with data-id set to the dispute id, showing event id, customer, amount (USD), reason and opened time. A Refresh button data-testid app-refresh re-fetches summary and list. ${COMMON}`;
  const t1=await appTurn(create,{reuse:progress.disputesApp,create:{name:'Disputes board',routes:['listDisputes','disputeSummary','closeDispute','ingestDispute']}});
  progress.disputesApp=t1.appId;saveProgress();
  const openCount=async()=>(await allRows('disputes')).filter(r=>r.data.status==='open').length;
  await expect(frame().getByTestId('tile-open')).toHaveText(String(await openCount()),{timeout:60000});
  await expect(frame().getByTestId('dispute-row')).toHaveCount(await openCount());
  await shot('11b-disputes-turn1');
  const refine=`Add a close action: each open row gets a button data-testid close-<id> (the dispute id) that opens an inline form with a text input data-testid close-reason and a submit button data-testid close-submit. Submitting calls closeDispute with the id and the reason (trimmed, required: when empty show data-testid close-error and do not call the backend), then refreshes list and summary. Backend errors show in close-error. ${COMMON}`;
  await appTurn(refine);
  const rows=(await allRows('disputes')).filter(r=>r.data.status==='open');
  const target=rows[0],openBefore=rows.length;
  await frame().getByTestId('close-'+target.id).click();
  await frame().getByTestId('close-submit').click();
  await expect(frame().getByTestId('close-error')).toBeVisible({timeout:15000});
  expect((await api('GET',(await tablePath('disputes'))+'/rows/'+target.id)).data.status).toBe('open');
  await frame().getByTestId('close-reason').fill('resolved in favour of the buyer');
  await frame().getByTestId('close-submit').click();
  await expect.poll(async()=>(await api('GET',(await tablePath('disputes'))+'/rows/'+target.id)).data,{timeout:30000}).toMatchObject({status:'closed',closed_reason:'resolved in favour of the buyer'});
  await expect(frame().getByTestId('dispute-row')).toHaveCount(openBefore-1,{timeout:30000});
  await expect(frame().getByTestId('tile-open')).toHaveText(String(openBefore-1));
  await shot('11b-disputes-turn2');
  await assertNoSecretsInApp(t1.appId);
  const usage=await api('GET',`/apps/${t1.appId}/api-usage`);
  expect(usage.map(u=>u.operation)).toEqual(expect.arrayContaining(['listDisputes','disputeSummary','closeDispute']));
 },{llm:true});

 // ------------------------------------------------------------------ 12 environments + release flow (+ staging)
 const releases=()=>api('GET',pp('/releases'));
 await step('s12','Settings: add staging; Release sheet: check code, fix secret inline, review routes, promote to prod',async()=>{
  const p=page();
  await p.goto(`${S()}?sheet=settings`);
  await g('settings-section-environments').click();
  await g('env-add').click();await g('env-new-name').fill('staging');
  await g('env-kind-staging-release').click();
  await expect(g('env-save')).toBeEnabled({timeout:15000});await g('env-save').click();
  await expect.poll(async()=>(await api('GET','/projects/'+P().id)).environments.map(e=>e.name)).toEqual(['dev','prod','staging']);
  await shot('12a-settings-environments');
  // release sheet: snapshot dev
  await p.goto(`${S()}?sheet=release`);
  await expect(g('release-create')).toBeEnabled({timeout:30000});
  await expect(g('release-checks-code')).toBeVisible();
  await shot('12b-release-snapshot');
  await g('release-create').click();
  await expect.poll(async()=>(await releases()).filter(r=>r.environment==='dev').length).toBe(1);
  // promote to prod: PAYMENTS_TOKEN is missing there -> blocked, fixed inline
  await expect(g('check-secrets_present')).toHaveAttribute('data-status','fail',{timeout:30000});
  await expect(g('release-promote')).toBeDisabled();
  await shot('12c-release-blocked');
  await g('secret-fix-PAYMENTS_TOKEN-form-more').click();
  await g('secret-fix-PAYMENTS_TOKEN-form-value').fill(SV.PAYMENTS_TOKEN);
  await g('secret-fix-PAYMENTS_TOKEN-form-hosts-input').fill('127.0.0.1');await g('secret-fix-PAYMENTS_TOKEN-form-hosts-input').press('Enter');
  await g('secret-fix-PAYMENTS_TOKEN-form-save').click();
  await g('secret-fix-rerun').click();
  await expect(g('check-secrets_present')).toHaveAttribute('data-status','pass',{timeout:30000});
  const sec=await api('GET',pp('/secrets'));
  expect(sec.filter(x=>x.key==='PAYMENTS_TOKEN').map(x=>x.environment).sort()).toEqual(['dev','prod']);
  expect(sec.every(x=>x.value===null)).toBe(true);
  await expect(g('release-promote')).toBeEnabled();
  await shot('12d-release-ready');
  await g('release-promote').click();await g('release-promote-confirm').click();
  await toast(/prod now serves release/);
  const rel=await releases();
  const active=rel.filter(r=>r.environment==='prod'&&r.is_active);
  expect(active).toHaveLength(1);progress.releaseV1=active[0].id;saveProgress();
  // prod serves the release through the router
  const st=await publicCall('GET','/refunds/stats');expect(st.status,st.text.slice(0,200)).toBe(200);expect(st.body.available).toBe(48250.5);expect(st.body.api_version).toBeUndefined();
  expect(privateIn(st.text)).toBe(false);
  // staging step: checks block it (no secrets there), nothing is promoted
  await p.goto(`${S()}?sheet=release`);
  await g('release-step-promote-staging').click();
  await expect(g('check-secrets_present')).toHaveAttribute('data-status','fail',{timeout:30000});
  await expect(g('release-promote')).toBeDisabled();
  await shot('12e-release-staging-blocked');
  expect((await releases()).filter(r=>r.environment==='staging')).toHaveLength(0);
 });

 // ------------------------------------------------------------------ 13 publish both apps, published pages show real data and follow backend changes
 async function publishUI(appId,slug){
  const p=page();
  await p.goto(`${S()}/apps/${appId}`);
  await g('builder-publish').click();
  const dlg=g('publish-dialog');await expect(dlg).toBeVisible();
  await dlg.getByTestId('publish-slug').fill(slug);
  await shot('13-publish-dialog-'+slug);
  await dlg.getByTestId('publish-submit').click();
  await expect(dlg.getByTestId('publish-success')).toBeVisible({timeout:90000});
  const detail=await api('GET','/apps/'+appId);
  expect(detail).toMatchObject({active:true,slug,access:'public'});
  return detail;
 }
 await step('s13','Publish both apps through the Publish dialog; published pages render real data and follow backend changes',async()=>{
  const ctx=context();
  const rslug='refunds-console-'+Date.now().toString(36),dslug='disputes-board-'+Date.now().toString(36);
  await publishUI(progress.refundsApp,rslug);
  await publishUI(progress.disputesApp,dslug);
  progress.rslug=rslug;progress.dslug=dslug;saveProgress();
  // refunds
  const rp=await ctx.newPage();
  await rp.goto(`${state.engineUrl}/a/${rslug}`);
  const nRef=await rowCount('refunds');
  await expect(rp.getByTestId('tile-count')).toHaveText(String(nRef),{timeout:60000});
  await expect(rp.getByTestId('tile-available')).toContainText('48,250');
  await expect(rp.getByTestId('refund-row')).toHaveCount(nRef);
  await rp.screenshot({path:out+'/13-published-refunds.png'});
  const c=await testRoute('createRefund',{body:{customer_id:'cust99',reason:'published refresh check',amount:19.99}});expect(c.status).toBe(200);
  await rp.getByTestId('app-refresh').click();
  await expect(rp.getByTestId('tile-count')).toHaveText(String(nRef+1),{timeout:30000});
  await expect(rp.getByTestId('refund-row')).toHaveCount(nRef+1);
  // the published page works against prod: prod served the release, the secret exists there
  const calls=await api('GET',pp('/runs?environment=prod&limit=100'));
  expect(calls.length).toBeGreaterThan(0);
  // disputes
  const dp=await ctx.newPage();
  await dp.goto(`${state.engineUrl}/a/${dslug}`);
  const open0=(await allRows('disputes')).filter(r=>r.data.status==='open').length;
  await expect(dp.getByTestId('tile-open')).toHaveText(String(open0),{timeout:60000});
  await expect(dp.getByTestId('dispute-row')).toHaveCount(open0);
  await dp.screenshot({path:out+'/13-published-disputes.png'});
  const ing=await testRoute('ingestDispute',{body:{id:'evt_published_'+Date.now(),type:'charge.dispute.created',data:{object:{customer:'cust5',amount:321,reason:'product_not_received'}}}});expect(ing.status).toBe(200);
  await dp.getByTestId('app-refresh').click();
  await expect(dp.getByTestId('tile-open')).toHaveText(String(open0+1),{timeout:30000});
  await expect(dp.getByTestId('dispute-row')).toHaveCount(open0+1);
  // bundles and pages never carry a secret value
  for(const [slug,pg] of [[rslug,rp],[dslug,dp]]){
   const html=await (await fetch(`${state.engineUrl}/a/${slug}`)).text();
   expect(privateIn(html)).toBe(false);
   expect(privateIn(await pg.evaluate(()=>document.documentElement.outerHTML))).toBe(false);
   const assets=[...html.matchAll(/(?:src|href)="([^"]+\.(?:js|css))"/g)].map(m=>m[1]);
   for(const a of assets){const u=a.startsWith('http')?a:state.engineUrl+(a.startsWith('/')?a:`/a/${slug}/${a}`);const t=await (await fetch(u)).text();expect(privateIn(t),'bundle '+a).toBe(false);}
   report.leak_checks['bundle_'+slug]={html_and_assets_checked:assets.length+1};
  }
  await rp.close();await dp.close();
 });

 // ------------------------------------------------------------------ 14 v2 release, API key, roll back to v1
 await step('s14','v2: edit backend, snapshot + promote, API key lifecycle, roll back to v1',async()=>{
  const p=page();
  const v2=refundsSource.replace(`"available": balance[0]["body"]["available"]}`,`"available": balance[0]["body"]["available"], "api_version": "v2"}`)+`

@api.get("/refunds/export", name="exportRefunds", auth="api_key")
def export_refunds(req):
    rows = refunds.list(limit=1000)
    return {"count": len(rows), "rows": rows}
`;
  expect(v2).toContain('api_version');
  await p.goto(`${S()}/backend/refunds`);
  await expect(g('code-editor').locator('.cm-content')).toBeVisible();
  await setSource(v2);await saveEditor();await toast(/Saved refunds\.py/);
  const dev=await testRoute('refundStats');expect(dev.status).toBe(200);expect(dev.body.api_version).toBe('v2');
  let prod=await publicCall('GET','/refunds/stats');expect(prod.status).toBe(200);expect(prod.body.api_version).toBeUndefined();
  expect((await publicCall('GET','/refunds/export')).status).toBe(422); // not a route yet in prod: falls to GET /refunds/{id}
  // snapshot dev, promote to prod
  await p.goto(`${S()}?sheet=release`);
  await g('release-step-snapshot').click();
  await expect(g('release-create')).toBeEnabled({timeout:30000});
  await shot('14a-release-v2-snapshot');
  await g('release-create').click();
  await expect.poll(async()=>(await releases()).filter(r=>r.environment==='dev').length).toBe(2);
  await expect(g('release-promote')).toBeEnabled({timeout:30000});
  await g('release-promote').click();await g('release-promote-confirm').click();
  await toast(/prod now serves release/);
  const rel=await releases();const act=rel.filter(r=>r.environment==='prod'&&r.is_active);
  expect(act).toHaveLength(1);expect(act[0].id).not.toBe(progress.releaseV1);
  prod=await publicCall('GET','/refunds/stats');expect(prod.status).toBe(200);expect(prod.body.api_version).toBe('v2');
  await shot('14b-release-v2-live');
  // API key lifecycle through Settings
  await p.goto(`${S()}?sheet=settings`);
  await g('settings-section-keys').click();
  await g('apikey-new').click();await g('apikey-name').fill('Marketplace export');
  await g('apikey-env-prod').click();
  await g('apikey-create').click();
  const key=await g('apikey-once-value').inputValue();expect(key.length).toBeGreaterThan(20);
  expect(privateIn(key)).toBe(false);
  const keys=await api('GET',pp('/api-keys'));expect(keys).toHaveLength(1);expect(JSON.stringify(keys)).not.toContain(key);
  await shot('14c-api-key-created');
  const withKey=await publicCall('GET','/refunds/export',undefined,{Authorization:'Bearer '+key});
  expect(withKey.status,withKey.text.slice(0,200)).toBe(200);expect(withKey.body.count).toBe(await rowCount('refunds'));
  expect((await publicCall('GET','/refunds/export')).status).toBe(401);
  await p.getByRole('button',{name:'I copied it'}).click();
  await g(`apikey-revoke-${keys[0].id}`).click();await g('apikey-revoke-confirm').click();
  await expect(g(`apikey-row-${keys[0].id}`)).toHaveAttribute('data-revoked','true');
  expect((await publicCall('GET','/refunds/export',undefined,{Authorization:'Bearer '+key})).status).toBe(401);
  // roll back prod to v1
  await p.goto(`${S()}?sheet=release`);
  await g('release-rollback-prod').click();
  await toast(/prod rolled back to release/);
  const act2=(await releases()).filter(r=>r.environment==='prod'&&r.is_active);
  expect(act2.map(r=>r.id)).toEqual([progress.releaseV1]);
  prod=await publicCall('GET','/refunds/stats');expect(prod.status).toBe(200);expect(prod.body.api_version).toBeUndefined();
  expect((await publicCall('GET','/refunds/export')).status).toBe(422);
  await shot('14d-rolled-back');
  // the published console still works after the rollback
  const rp=await context().newPage();await rp.goto(`${state.engineUrl}/a/${progress.rslug}`);
  await expect(rp.getByTestId('tile-count')).toHaveText(String(await rowCount('refunds')),{timeout:60000});await rp.close();
 });

 // ------------------------------------------------------------------ 15 history: ~300 runs over 30 days (both environments)
 await step('s15','History: ~300 runs across 30 days; Runs view ranges, filters, Load older; Overview 30-day grid',async()=>{
  const p=page();
  const customer=(await allRows('customers'))[0];
  const ids=[];
  const t0=Date.now();
  for(let i=0;i<300;i++){
   const failing=i%7===3;                       // ~14% failures
   const env=i%3===0?'dev':'prod';
   let id;
   if(failing){const r=await testRoute('customerRiskSecondary',{env:'dev',params:{id:String(customer.id)}});expect(r.status).toBe(502);id=r.execution_id||r.run_id;}
   else if(env==='prod'){const r=await publicCall('GET',i%2?'/refunds':'/refunds/stats');expect(r.status).toBe(200);id=r.headers.get('x-execution-id');}
   else{const r=await testRoute(i%2?'listRefunds':'refundStats');expect(r.status).toBe(200);id=r.execution_id||r.run_id;}
   ids.push(id);
  }
  report.history_seed_ms=Date.now()-t0;
  const known=ids.filter(Boolean);
  expect(known.length,'execution ids captured').toBeGreaterThanOrEqual(250);
  // spread them over 30 days: newest 40 stay today, the rest are shifted back 1..29 days (and a few hours) in the throwaway DB
  const shifts=known.map((id,i)=>{if(!/^[\w-]+$/.test(id))throw Error('bad id');const age=i<40?0:1+((i*13)%29);return [id,age*24+((i*5)%20)];});
  const values=shifts.map(([id,h])=>`('${id}', ${Number(h)})`).join(',');
  sql(`UPDATE "workflow-app".executions e SET start_time = e.start_time - (v.h * interval '1 hour'), end_time = e.end_time - (v.h * interval '1 hour') FROM (VALUES ${values}) AS v(id,h) WHERE e.id = v.id`);
  const daily=await api('GET',pp('/runs/daily?days=30'));
  expect(daily).toHaveLength(30);
  const total=daily.reduce((n,d)=>n+d.total,0),failed=daily.reduce((n,d)=>n+d.failed,0);
  expect(total).toBeGreaterThanOrEqual(known.length);expect(failed).toBeGreaterThanOrEqual(40);
  expect(daily.filter(d=>d.total>0).length).toBeGreaterThanOrEqual(25);
  // Runs view
  await p.goto(`${S()}/runs?env=all`);
  await expect(g('runs-view')).toBeVisible();
  const all=[];{let cur=null;for(let i=0;i<20;i++){const r=await raw('GET',pp(`/runs?limit=500${cur?'&before='+encodeURIComponent(cur):''}`));if(r.status!==200)throw Error('runs list '+r.status);all.push(...r.body);cur=r.headers.get('x-next-cursor');if(!cur)break;}}
  const ago=(ms)=>Date.now()-ms;const startedAfter=(ms)=>all.filter(r=>new Date(r.start_time).getTime()>=ago(ms)).length;
  const n24=startedAfter(86400000);
  await expect(g('runs-subtitle')).toContainText(String(Math.min(50,n24)),{timeout:30000});   // the first page holds 50 runs
  await shot('15a-runs-24h');
  const pick=async(key)=>{await g('runs-range').click();await g('runs-range-'+key).click();};
  await pick('all');
  await expect(g('run-row')).toHaveCount(50,{timeout:30000});
  await expect(g('runs-load-more')).toBeVisible();
  for(let i=0;i<40&&await g('runs-load-more').count();i++){await g('runs-load-more').click({timeout:4000}).catch(()=>{});await p.waitForTimeout(500);}
  await expect(g('run-row')).toHaveCount(all.length,{timeout:30000});
  await expect(g('runs-count')).toContainText(`${all.length} runs`);
  await shot('15b-runs-all-loaded');
  await pick('7d');await expect(g('run-row')).toHaveCount(startedAfter(7*86400000),{timeout:30000});
  await pick('24h');await expect(g('run-row')).toHaveCount(n24,{timeout:30000});
  await pick('all');
  await g('runs-seg-err').click();
  await expect.poll(()=>g('run-row').count()).toBeGreaterThan(0);
  const failedRows=await g('run-row').evaluateAll(rs=>rs.map(r=>r.getAttribute('data-status')));
  expect(failedRows.length).toBeGreaterThan(0);expect(failedRows.every(x=>x==='failed')).toBe(true);
  await expect(g('run-status-code').first()).toHaveText('502');
  await g('runs-seg-all').click();
  await p.getByTestId('env-prod').click();
  await expect.poll(()=>g('run-row').count(),{timeout:20000}).toBeGreaterThan(0);
  const envs=await g('run-row').evaluateAll(rs=>rs.map(r=>r.getAttribute('data-env')));expect(envs.length).toBeGreaterThan(0);expect(envs.every(e=>e==='prod')).toBe(true);
  // failed run: expanded detail with the stack trace; frame click lands on the module line
  await p.goto(`${S()}/runs?env=all`);await pick('all');await g('runs-env-all').click();await g('runs-seg-err').click();
  await expect.poll(()=>g('run-row').count(),{timeout:30000}).toBeGreaterThan(0);
  await g('run-row').first().click();
  await expect(g('run-detail-stack')).toBeVisible();
  await shot('15c-runs-failed-expanded');
  // Overview: the 30-day grid matches the daily aggregate
  await p.goto(S());
  const cells=g('activity-30d').locator('[role="gridcell"]');
  await expect(cells).toHaveCount(30,{timeout:30000});
  const gt=await cells.evaluateAll(cs=>cs.reduce((n,c)=>n+Number(c.getAttribute('data-count')),0));
  const gf=await cells.evaluateAll(cs=>cs.reduce((n,c)=>n+Number(c.getAttribute('data-failed')),0));
  expect([gt,gf]).toEqual([total,failed]);
  await shot('15d-overview-activity');
  report.history={runs_created:known.length,daily_total:total,daily_failed:failed,days_with_runs:daily.filter(d=>d.total>0).length};
 });

 // ------------------------------------------------------------------ 16 final pages in both themes + secret-leak scan
 await step('s16','Final state of every page in both themes; secret values nowhere (source, bundles, runs, logs, responses)',async()=>{
  const p=page();
  const wf=progress.workflowId;
  const pages=[
   ['projects-home',state.studioUrl+'/projects'],
   ['overview',S()],
   ['backend-refunds',S()+'/backend/refunds?route=refundStats'],
   ['backend-disputes',S()+'/backend/disputes'],
   ['data-customers',S()+'/data/customers'],
   ['data-refunds',S()+'/data/refunds'],
   ['runs',S()+'/runs?env=all'],
   ['secrets',S()+'/secrets'],
   ['workflows',S()+'/workflows'],
   ['workflow-editor',`${S()}/workflows/${wf}`],
   ['builder-refunds',`${S()}/apps/${progress.refundsApp}`],
   ['builder-disputes',`${S()}/apps/${progress.disputesApp}`],
   ['release-sheet',S()+'?sheet=release'],
   ['settings-sheet',S()+'?sheet=settings'],
  ];
  for(const scheme of ['light','dark']){
   await p.emulateMedia({colorScheme:scheme,reducedMotion:'no-preference'});
   for(const [name,url] of pages){
    await p.goto(url);await p.waitForLoadState('networkidle').catch(()=>{});await p.waitForTimeout(700);
    if(scheme==='dark')await expect(p.locator('html')).toHaveClass(/dark/);else await expect(p.locator('html')).not.toHaveClass(/dark/);
    await p.screenshot({path:`${out}/final-${scheme}-${name}.png`});
   }
   // palette
   await p.goto(S());await expect(g('overview-panel')).toBeVisible();await p.locator('body').click({position:{x:5,y:400}});await p.keyboard.press('ControlOrMeta+k');await expect(g('command-palette')).toBeVisible();await p.screenshot({path:`${out}/final-${scheme}-palette.png`});await p.keyboard.press('Escape');
  }
  await p.emulateMedia({colorScheme:null,reducedMotion:null});
  // ---- secret leak scan over every API surface
  const surfaces={};
  const mods=await api('GET',pp('/modules'));
  surfaces.modules=JSON.stringify(await Promise.all(mods.map(m=>api('GET',pp('/modules/'+m.id)))));
  surfaces.routes=JSON.stringify(await routes());
  surfaces.apps=JSON.stringify(await Promise.all([progress.refundsApp,progress.disputesApp].map(id=>api('GET','/apps/'+id))));
  surfaces.client_dev=await (await fetch(`${state.engineUrl}/api/projects/${P().id}/client.ts?environment=dev`)).text();
  surfaces.client_prod=await (await fetch(`${state.engineUrl}/api/projects/${P().id}/client.ts?environment=prod`)).text();
  surfaces.openapi=JSON.stringify(await api('GET',pp('/openapi.json')));
  surfaces.releases=JSON.stringify(await releases());
  surfaces.secrets=JSON.stringify(await api('GET',pp('/secrets')));
  surfaces.workflows=JSON.stringify(await Promise.all((await api('GET',pp('/workflows'))).map(w=>api('GET','/workflows/'+w.id))));
  surfaces.project=JSON.stringify(await api('GET','/projects/'+P().id));
  const runIds=[];{let cur=null;for(let i=0;i<6;i++){const r=await raw('GET',pp(`/runs?limit=500${cur?'&before='+encodeURIComponent(cur):''}`));runIds.push(...r.body.map(x=>x.id));cur=r.headers.get('x-next-cursor');if(!cur)break;}}
  surfaces.run_list=JSON.stringify(runIds);
  let runChunks=[];for(const id of runIds.slice(0,400))runChunks.push(JSON.stringify(await api('GET',pp('/runs/'+id))));
  surfaces.run_details=runChunks.join('\n');
  for(const t of ['customers','refunds','disputes','events'])surfaces['rows_'+t]=JSON.stringify(await allRows(t));
  const hits={};
  for(const [k,v] of Object.entries(surfaces)){const found=Object.entries(SV).filter(([n,val])=>v.includes(val)).map(([n])=>n);if(found.length)hits[k]=found;}
  report.leak_checks.api_surfaces={checked:Object.keys(surfaces),run_details_checked:Math.min(400,runIds.length),hits};
  expect(hits).toEqual({});
  // engine / vite logs (warning level) as they stand now
  for(const f of ['engine.log','vite.log']){const t=fs.readFileSync(`${state.tmp}/${f}`,'utf8');report.leak_checks[f]={bytes:t.length,contains_secret_values:Object.values(SV).some(v=>t.includes(v))};expect(report.leak_checks[f].contains_secret_values).toBe(false);}
  // the upstream saw the real values (proof the secrets were actually used, not blanked)
  const log=await (await fetch(U+'/__log')).json();
  report.leak_checks.upstream_received_real_secrets={balance:log.some(l=>l.path==='/v1/balance'&&l.auth_ok),risk:log.some(l=>l.path.startsWith('/v1/risk')&&l.risk_key_ok)};
  expect(report.leak_checks.upstream_received_real_secrets).toEqual({balance:true,risk:true});
  expect(leaks_ref.length,'response bodies carrying a secret').toBe(0);
 });
};
