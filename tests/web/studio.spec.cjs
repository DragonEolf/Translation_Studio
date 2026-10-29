/* Browser checks use mocked services: no PDFs or text leave this machine.
   Requires Playwright's chromium browser. Run with: node tests/web/studio.spec.cjs */
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { chromium } = require('playwright');
const web = path.resolve(__dirname, '../../app/web');
const output = path.resolve(__dirname, '../../output/ui-verification');
const health = {transliteration_configured:true,supported_languages:['en','hi','ta','ur'],available_font_languages:['en','hi','ta','ur'],max_upload_mb:25,max_pages:100,translation_configured:true,ocr_configured:true};
let browser;
async function setup(scenario = {}) {
  const context = await browser.newContext();
  const page = await context.newPage();
  const state = {uploads:0,translations:0,jobReads:0,payload:null,failPoll:Boolean(scenario.failPoll),...scenario};
  const errors = []; page.on('pageerror', error => errors.push(error.message));
  await page.route('http://studio.test/**', async route => {
    const url = new URL(route.request().url()), pathname = url.pathname;
    const json = (body, status = 200) => route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});
    if (pathname === '/' || pathname.startsWith('/assets/')) {
      const file = pathname === '/' ? 'index.html' : path.basename(pathname);
      return route.fulfill({contentType:file.endsWith('.html')?'text/html':file.endsWith('.css')?'text/css':file.endsWith('.png')?'image/png':'application/javascript',body:await fs.readFile(path.join(web,file))});
    }
    if (pathname === '/v1/health') return state.healthDown ? route.abort() : json({...health,...state.health});
    if (pathname === '/v1/documents' && route.request().method() === 'POST') {
      state.uploads++;
      if (state.uploadError) return json({error:{code:state.uploadError,message:'Server upload error'}},413);
      return json({document_id:'doc',job_id:'inspect',status:'queued'},202);
    }
    if (pathname === '/v1/jobs/inspect') return json({status:state.inspectError?'failed':'completed',progress:1,error_code:state.inspectError});
    if (pathname === '/v1/documents/doc/pages') return json(state.pages || [{metadata_json:{extraction_method:'digital'}}]);
    if (pathname === '/v1/documents/doc/translations') {
      state.translations++; state.payload = route.request().postDataJSON();
      if (state.lostSubmission) { state.lostSubmission=false; return route.abort(); }
      return json({job_id:'translate'},202);
    }
    if (pathname === '/v1/documents/doc/transliteration-candidates') return json({candidates:state.noWords?[]:[{word:'VGF',count:2},{word:'API',count:1}],max_words:20});
    if (pathname === '/v1/documents/doc/transliteration-preview') {
      state.previewPayload=route.request().postDataJSON(); state.previewCalls=(state.previewCalls || 0)+1;
      if (state.losePreview) { state.losePreview=false; state.originalPreviewId=state.previewPayload.request_id; return route.abort(); }
      return json({job_id:'preview'},202);
    }
    if (pathname === '/v1/jobs/preview') return json({id:'preview',status:'completed',progress:1,payload:{suggestions:state.previewPayload.words.map(word=>({word,replacement:word==='VGF'?'வீ ஜி எஃப்':'ஏ பி ஐ'}))}});
    if (pathname === '/v1/documents/doc/transliteration-apply') { state.applyPayload=route.request().postDataJSON(); return json({job_id:'render'},202); }
    if (pathname === '/v1/jobs/render') return json({status:'completed',progress:1});
    if (pathname === '/v1/jobs/translate/cancel') {
      state.cancelCalls=(state.cancelCalls || 0)+1;
      if (state.cancelFails) { state.cancelFails=false; return route.abort(); }
      state.cancelled=true;
      return json({status:'cancelling'});
    }
    if (pathname === '/v1/jobs/translate') {
      state.jobReads++;
      if (state.failPoll) return route.abort();
      return json({status:state.cancelled?'cancelled':state.jobError?'failed':state.running?'running':state.partial?'completed_with_issues':'completed',progress:state.running ?? 1,error_code:state.jobError});
    }
    if (pathname === '/v1/documents/doc') return json({status:state.partial?'completed_with_issues':'completed',latest_job:{id:state.translations?'translate':'inspect',operation:state.translations?'translate':'inspect',status:'completed'},translation_summary:state.summary || {total_blocks:10,rendered_blocks:10,retained_source_blocks:0},issue_summary:state.issues || []});
    throw new Error('Unexpected request: ' + pathname);
  });
  await page.goto('http://studio.test/');
  await page.waitForFunction(() => document.querySelector('#language').options.length > 1 || !document.querySelector('#connection-note').hidden);
  return {context,page,state,errors};
}
const file = {name:'sample.pdf',mimeType:'application/pdf',buffer:Buffer.from('%PDF-1.7 test fixture')};
async function start(page) { await page.locator('#pdf-file').setInputFiles(file); await page.locator('#submit').click(); }
async function completed(page) { await page.locator('#download').waitFor({state:'visible'}); }
async function test(name, scenario, fn) {
  const env = await setup(scenario);
  try { await fn(env); assert.deepEqual(env.errors,[]); console.log('PASS',name); }
  finally { await env.context.close(); }
}
(async () => {
  await fs.mkdir(output,{recursive:true});
  browser = await chromium.launch({headless:true});
  await test('initial view, native names, responsive layout',{},async ({page}) => {
    assert.match(await page.locator('#language').innerText(),/தமிழ்/);
    await page.locator('.iitm-logo').evaluate(img => img.decode());
    assert.equal(await page.locator('.iitm-logo').evaluate(img => img.naturalWidth > 0),true);
    assert.match(await page.locator('.maker-credit').innerText(),/Krishna Karthikeyan/);
    assert.equal(await page.locator('input[value="auto"]').isChecked(),true);
    assert.equal(await page.locator('#submit').isDisabled(),true);
    await page.screenshot({path:path.join(output,'desktop.png'),fullPage:true});
    for (const width of [320,390,768,1280]) {
      await page.setViewportSize({width,height:1000});
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),true);
    }
    await page.setViewportSize({width:390,height:1000});
    await page.screenshot({path:path.join(output,'mobile.png'),fullPage:true});
  });
  await test('initial transliteration option follows language and reaches request',{},async ({page,state}) => {
    await page.locator('#language').selectOption('en');
    assert.equal(await page.locator('#auto-transliterate').isDisabled(),true);
    await page.locator('#language').selectOption('ta');
    await page.locator('#auto-transliterate').check();
    await start(page); await completed(page);
    assert.equal(state.payload.transliterate,true);
  });
  await test('reject wrong type and empty PDF before upload',{},async ({page,state}) => {
    await page.locator('#pdf-file').setInputFiles({...file,name:'notes.txt'});
    assert.match(await page.locator('#file-error').innerText(),/Choose a PDF/);
    await page.locator('#pdf-file').setInputFiles({...file,buffer:Buffer.alloc(0)});
    assert.match(await page.locator('#file-error').innerText(),/empty/);
    assert.equal(state.uploads,0);
  });
  await test('reject oversized PDF before upload',{health:{max_upload_mb:1}},async ({page,state}) => {
    await page.locator('#pdf-file').setInputFiles({...file,buffer:Buffer.alloc(1048577)});
    assert.match(await page.locator('#file-error').innerText(),/exceeds the 1 MB limit/);
    assert.equal(state.uploads,0);
  });
  for (const layout of ['auto','paragraphs','spans']) await test('successful '+layout+' request',{},async ({page,state}) => {
    await page.locator(`input[value="${layout}"]`).check();
    await start(page); await completed(page);
    assert.equal(state.payload.layout_mode,layout);
    assert.equal(state.translations,1);
    assert.equal(await page.locator('#download').getAttribute('href'),'/v1/documents/doc/artifacts/final.pdf?v=translate');
    assert.equal(await page.locator('#review-note').isVisible(),false);
    await page.locator('#new-document').click();
    assert.equal(await page.locator('#result').isVisible(),false);
    assert.equal(await page.locator('#submit').isDisabled(),true);
  });
  await test('scan, blank page and retained-source warnings',{partial:true,pages:[{metadata_json:{extraction_method:'ocr'}},{metadata_json:{extraction_method:'blank'}},{metadata_json:{extraction_method:'digital'}}],summary:{total_blocks:10,rendered_blocks:8,retained_source_blocks:2},issues:[{code:'unsupported_glyphs'},{code:'collision'},{code:'ocr_layout_estimated'}]},async ({page}) => {
    await start(page); await completed(page);
    assert.match(await page.locator('#document-note').innerText(),/1 scanned.*1 blank/);
    assert.match(await page.locator('#review-summary').innerText(),/2 text regions remain/);
    assert.equal(await page.locator('#review-reasons li').count(),3);
    await page.screenshot({path:path.join(output,'review.png'),fullPage:true});
  });
  await test('blank PDF does not claim translation',{summary:{total_blocks:0,rendered_blocks:0,retained_source_blocks:0}},async ({page}) => {
    await start(page); await completed(page);
    assert.match(await page.locator('#status-title').innerText(),/no text found/);
  });
  for (const [code,pattern] of [['encrypted_pdf',/password-protected/],['too_many_pages',/100-page limit/],['invalid_pdf',/fresh PDF/],['ocr_authentication_failed',/OCR credentials/]]) await test(code,{inspectError:code},async ({page,state}) => {
    await start(page); await page.locator('#new-document').waitFor({state:'visible'});
    assert.match(await page.locator('#status-detail').innerText(),pattern);
    assert.equal(state.translations,0);
    assert.equal(await page.locator('#download').isVisible(),false);
  });
  for (const [code,pattern] of [['provider_unavailable',/after retries/],['invalid_api_key',/credentials/]]) await test(code,{jobError:code},async ({page}) => {
    await start(page); await page.locator('#new-document').waitFor({state:'visible'});
    assert.match(await page.locator('#status-detail').innerText(),pattern);
  });
  await test('reconstruction failure exposes draft only',{jobError:'reconstruction_failed'},async ({page}) => {
    await start(page); await page.locator('#draft').waitFor({state:'visible'});
    assert.equal(await page.locator('#download').isVisible(),false);
    assert.equal(await page.locator('#draft').getAttribute('href'),'/v1/documents/doc/artifacts/draft.pdf');
  });
  await test('lost submission response reconnects without duplicate POST',{lostSubmission:true},async ({page,state}) => {
    await start(page); await page.locator('#resume').waitFor({state:'visible'});
    assert.equal(await page.locator('#submit').isDisabled(),true);
    await page.locator('#resume').click(); await completed(page);
    assert.equal(state.translations,1);
  });
  await test('poll outage survives page reload without new translation',{failPoll:true},async ({page,state}) => {
    await page.locator('input[value="spans"]').check();
    await start(page); await page.locator('#resume').waitFor({state:'visible'});
    state.failPoll=false; await page.reload(); await completed(page);
    assert.equal(await page.locator('input[value="spans"]').isChecked(),true);
    assert.equal(state.uploads,1); assert.equal(state.translations,1);
  });
  await test('rendering stage locks language and layout controls',{running:.8},async ({page,state}) => {
    await start(page); await page.waitForFunction(() => document.querySelector('#status-title').textContent === 'Preparing your download');
    assert.equal(await page.locator('input[value="spans"]').isDisabled(),true);
    assert.equal(await page.locator('#language').isDisabled(),true);
    assert.equal(await page.locator('[data-step="render"]').getAttribute('aria-current'),'step');
    state.running=false; await completed(page);
  });
  await test('cancel stops a running translation and unlocks controls',{running:.3},async ({page,state}) => {
    await start(page); await page.waitForFunction(() => document.querySelector('#status-title').textContent === 'Translating your document');
    await page.locator('#cancel').click();
    await page.waitForFunction(() => document.querySelector('#status-title').textContent === 'Translation cancelled');
    assert.equal(state.cancelCalls,1);
    assert.equal(await page.locator('#download').isVisible(),false);
    assert.equal(await page.locator('#cancel').isVisible(),false);
    assert.equal(await page.locator('#language').isDisabled(),false);
    assert.equal(await page.evaluate(() => sessionStorage.getItem('translation-studio-active-job')),null);
  });
  await test('cancel can be retried after a lost response',{running:.3,cancelFails:true},async ({page,state}) => {
    await start(page); await page.waitForFunction(() => document.querySelector('#status-title').textContent === 'Translating your document');
    await page.locator('#cancel').click();
    await page.waitForFunction(() => document.querySelector('#status-detail').textContent.includes('Could not confirm cancellation'));
    assert.equal(await page.locator('#cancel').isEnabled(),true);
    await page.locator('#cancel').click();
    await page.waitForFunction(() => document.querySelector('#status-title').textContent === 'Translation cancelled');
    assert.equal(state.cancelCalls,2);
  });
  await test('remaining English can be selected, previewed and applied',{},async ({page,state}) => {
    await page.locator('#language').selectOption('ta'); await start(page); await completed(page);
    await page.locator('#transliterate').click();
    assert.equal(await page.locator('#generate-transliteration').isDisabled(),true);
    await page.locator('#transliteration-words input[value="VGF"]').check();
    await page.locator('#transliteration-words input[value="API"]').check();
    await page.locator('#generate-transliteration').click();
    await page.locator('#apply-transliteration').waitFor({state:'visible'});
    assert.deepEqual(state.previewPayload.words,['VGF','API']);
    assert.match(await page.locator('#transliteration-words').innerText(),/வீ ஜி எஃப்/);
    assert.equal(state.applyPayload,undefined);
    await page.screenshot({path:path.join(output,'transliteration.png'),fullPage:true});
    await page.locator('#transliteration-words input[value="API"]').uncheck();
    await page.locator('#apply-transliteration').click(); await completed(page);
    assert.deepEqual(state.applyPayload,{preview_job_id:'preview',words:['VGF']});
  });
  await test('no remaining English makes no transliteration call',{noWords:true},async ({page,state}) => {
    await start(page); await completed(page); await page.locator('#transliterate').click();
    await page.waitForFunction(()=>document.querySelector('#transliteration-message').textContent.includes('No eligible English'));
    assert.equal(await page.locator('#generate-transliteration').isVisible(),false);
    assert.equal(state.previewCalls,undefined);
  });
  await test('lost pronunciation submission reuses its request ID',{losePreview:true},async ({page,state}) => {
    await start(page); await completed(page); await page.locator('#transliterate').click();
    await page.locator('#transliteration-words input[value="VGF"]').check();
    await page.locator('#generate-transliteration').click();
    await page.waitForFunction(()=>document.querySelector('#transliteration-message').textContent.includes('connection'));
    await page.locator('#generate-transliteration').click();
    await page.locator('#apply-transliteration').waitFor({state:'visible'});
    assert.equal(state.previewPayload.request_id,state.originalPreviewId);
  });
  await test('legacy repair status has no UI controls',{health:{vision_review_enabled:true}},async ({page}) => {
    assert.equal(await page.locator('#vision-note').count(),0);
  });
  await test('missing fonts are explained',{health:{available_font_languages:['en']}},async ({page}) => {
    assert.match(await page.locator('#font-note').innerText(),/font is not installed/);
  });
  await test('missing service configuration is explained',{health:{translation_configured:false}},async ({page}) => {
    assert.match(await page.locator('#connection-message').innerText(),/not configured/);
    await page.locator('#pdf-file').setInputFiles(file);
    assert.equal(await page.locator('#submit').isDisabled(),true);
  });
  await test('server startup connection can be retried',{healthDown:true},async ({page,state}) => {
    assert.match(await page.locator('#connection-message').innerText(),/Cannot connect/);
    state.healthDown=false; await page.locator('#reconnect').click();
    await page.waitForFunction(() => document.querySelector('#connection-note').hidden);
  });
  console.log('All browser checks passed. Screenshots:',output);
})().catch(error => { console.error(error); process.exitCode=1; }).finally(async () => { if (browser) await browser.close(); });
