/** Independent SQL oracles for a browser-driven case write, not fixture responses. */
const {execFileSync}=require('child_process');
const {expect}=require(process.env.DASHBOARD_ROOT+'/apps/workflow-studio/node_modules/@playwright/test');
function readDatabase(state){
 const code=`import json,psycopg2
from pathlib import Path
cfg=json.loads(Path(${JSON.stringify(process.env.DASHBOARD_CREDENTIAL_FILE)}).read_text())
with psycopg2.connect(host=${JSON.stringify(state.tmp)},port=cfg['port'],dbname=cfg['secrets']['PG_DATABASE']) as conn:
 with conn.cursor() as c:
  c.execute("SELECT state,owner FROM bank_cases WHERE id='CASE-000600'");case=c.fetchone()
  c.execute("SELECT count(*) FROM bank_case_actions WHERE case_id='CASE-000600'");count=c.fetchone()[0]
  c.execute("SELECT state,owner,note FROM bank_case_actions WHERE case_id='CASE-000600' ORDER BY id DESC LIMIT 1");last=c.fetchone()
  c.execute("SELECT amount,status FROM bank_transactions WHERE id='TX-000600'");transaction=c.fetchone()
print(json.dumps({'case':case,'actions':count,'last':last,'transaction':[str(transaction[0]),transaction[1]]}))`;
 return JSON.parse(execFileSync(process.env.DASHBOARD_PYTHON,['-c',code],{encoding:'utf8'}));
}
exports.checkWrites=async({root,page,state,report,out,label})=>{
 console.log('Checking real UI case update and independent SQL:',label);
 await root.getByTestId('nav-transactions').click();
 await expect(root.getByTestId('transaction-row')).toHaveCount(1);
 await expect(root.getByTestId('page-next')).toBeDisabled();await expect(root.getByTestId('page-prev')).toBeDisabled();
 await root.getByTestId('transaction-search').fill("' OR 1=1 --");await root.getByTestId('apply-filters').click();
 await expect(root.getByTestId('transactions-total')).toHaveText('0');await expect(root.getByTestId('empty-state')).toBeVisible();
 await root.getByTestId('transaction-search').fill('');await root.getByTestId('apply-filters').click();await expect(root.getByTestId('transaction-row')).toHaveCount(25);
 await root.getByTestId('page-next').click();await expect(root.getByTestId('current-page')).toHaveText('2');await root.getByTestId('page-prev').click();await expect(root.getByTestId('current-page')).toHaveText('1');
 const before=readDatabase(state);
 expect(before.actions).toBe(label==='preview'?1:2);
 await root.getByTestId('nav-exceptions').click();await expect(root.getByTestId('case-row')).toHaveCount(100);
 await root.getByTestId('edit-case-CASE-000600').click();await expect(root.getByTestId('case-editor')).toBeVisible();
 await expect(root.getByTestId('case-editor')).toContainText('CASE-000600');
 await expect(root.getByTestId('case-history')).toContainText('Initial independent API verification');
 await root.getByTestId('case-owner').fill('Jordan Rivera');await root.getByTestId('case-note').fill('');await root.getByTestId('case-save').click();
 await expect(root.getByRole('alert').first()).toBeVisible();expect(readDatabase(state)).toEqual(before);
 const target=label==='preview'?'in_review':'resolved',note='Verified '+label+" investigation O'Brien <b data-testid=\"injected-note\">reviewed</b>";
 await root.getByTestId('case-state').selectOption(target);await root.getByTestId('case-note').fill(note);await root.getByTestId('case-save').click();
 await expect(root.getByTestId('case-saved')).toBeVisible({timeout:20000});
 await expect.poll(()=>readDatabase(state).actions).toBe(before.actions+1);
 const after=readDatabase(state);expect(after.case).toEqual([target,'Jordan Rivera']);expect(after.last).toEqual([target,'Jordan Rivera',note]);expect(after.transaction).toEqual(before.transaction);
 if(await root.getByTestId('case-editor').isVisible())await root.getByTestId('case-cancel').click();
 await expect(root.getByTestId('case-row').filter({hasText:'CASE-000600'})).toContainText('Jordan Rivera');
 await root.getByTestId('edit-case-CASE-000600').click();await expect(root.getByTestId('case-state')).toHaveValue(target);await expect(root.getByTestId('case-owner')).toHaveValue('Jordan Rivera');await expect(root.getByTestId('case-history')).toContainText(note);await expect(root.getByTestId('injected-note')).toHaveCount(0);await root.getByTestId('case-cancel').click();
 await page.screenshot({path:out+'/'+label+'-case-updated.png'});
 report.checks[label+'_writes']={pagination_bounds_and_previous:true,literal_injection_search:true,invalid_form_did_not_write:true,case_state:target,owner:'Jordan Rivera',audit_count:after.actions,one_atomic_audit_insert:true,unrelated_transaction_unchanged:true,read_after_write:true,literal_note_text_is_not_markup:true};
 if(label==='published'){
  await page.reload();await root.getByTestId('nav-exceptions').click();await root.getByTestId('edit-case-CASE-000600').click();await expect(root.getByTestId('case-state')).toHaveValue(target);await expect(root.getByTestId('case-history')).toContainText(note);await expect(root.getByTestId('injected-note')).toHaveCount(0);await root.getByTestId('case-cancel').click();
  await root.getByTestId('nav-overview').click();await expect(root.getByTestId('kpi-transactions')).toHaveText('600');await expect(root.getByTestId('trend-bar')).toHaveCount(15);
  await root.getByTestId('theme-toggle').click();
  const contrast=()=>root.getByTestId('kpi-transactions').evaluate(el=>{
    const ctx=document.createElement('canvas').getContext('2d');
    const rgb=color=>{ctx.clearRect(0,0,1,1);ctx.fillStyle=color;ctx.fillRect(0,0,1,1);return [...ctx.getImageData(0,0,1,1).data]};
    let p=el;while(p&&rgb(getComputedStyle(p).backgroundColor)[3]!==255)p=p.parentElement;
    const fg=rgb(getComputedStyle(el).color),bg=rgb(getComputedStyle(p||document.body).backgroundColor);
    const lum=values=>values.slice(0,3).map(c=>c/255).map(c=>c<=.04045?c/12.92:((c+.055)/1.055)**2.4).reduce((sum,c,i)=>sum+c*[.2126,.7152,.0722][i],0);
    const l1=lum(fg),l2=lum(bg);return (Math.max(l1,l2)+.05)/(Math.min(l1,l2)+.05)
  });
  await expect.poll(contrast).toBeGreaterThanOrEqual(4.5);await page.screenshot({path:out+'/published-dark.png',animations:'disabled'});report.checks.dark_kpi_contrast=await contrast();await root.getByTestId('theme-toggle').click();
  for(const width of [1024,390]){await page.setViewportSize({width,height:900});await expect(root.getByTestId('nav-transactions')).toBeVisible();await root.getByTestId('nav-transactions').click();await expect(root.getByTestId('transaction-row')).toHaveCount(25);
   await root.getByTestId('nav-accounts').click();await expect(root.getByTestId('account-row')).toHaveCount(40);
   await root.getByTestId('nav-exceptions').click();await expect(root.getByTestId('case-row')).toHaveCount(100);
   await root.getByTestId('edit-case-CASE-000600').click();await expect(root.getByTestId('case-editor')).toBeVisible();await page.keyboard.press('Escape');await expect(root.getByTestId('case-editor')).toHaveCount(0);await expect(root.getByTestId('edit-case-CASE-000600')).toBeFocused();
   await root.getByTestId('nav-transactions').click();await expect(root.getByTestId('transaction-row')).toHaveCount(25);expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);await page.screenshot({path:out+'/published-responsive-'+width+'.png'});}
  await page.setViewportSize({width:1440,height:900});report.checks.published_reload_theme_and_responsive=true;
 }
};
