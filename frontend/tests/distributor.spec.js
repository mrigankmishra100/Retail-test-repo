import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

const knowledge = { ready:true, demo_data:true, document_name:'Distributor_DEMO.md', version:'v1' };
const answer = { answer:'BlueDart offers a documented delivery option. [source-1]', demo_data:true,
  sources:[{source_id:'source-1', document_name:'Distributor_DEMO.md', excerpt:'BlueDart demo delivery policy.',page:1}] };

async function setup(page, options = {}) {
  const calls = [], errors = [];
  const session = {session_id:'distributor-test',session_token:'test-token',role:'distributor',supplier_id:null};
  page.on('pageerror', error => errors.push(error.message));
  await page.route('https://static.oracle.com/**', route => route.abort());
  await page.route('**/api/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname.replace(/^\/api/, '');
    calls.push({path,method:request.method(),body:request.postDataJSON()});
    if (path === '/sessions') return route.fulfill({json:session,status:options.loginFailure ? 422 : 201});
    if (path === '/sessions/me') return route.fulfill(request.method() === 'DELETE' ? {status:204} : {json:session});
    if (path === '/health') return route.fulfill({json:{status:'ok'}});
    if (path === '/distributor/knowledge') return route.fulfill({json:options.knowledge || knowledge,status:options.knowledgeStatus || 200});
    if (path === '/distributor/questions/ask') return route.fulfill({json:options.answer || answer,status:options.answerStatus || 200});
    return route.fulfill({status:404,json:{detail:`Unexpected route: ${path}`}});
  });
  await page.goto('/');
  return {calls,errors};
}

async function enter(page) {
  await page.getByRole('radio', {name:/Distributor/}).check();
  await expect(page.getByLabel('Supplier ID')).toHaveCount(0);
  await page.getByRole('button',{name:'Enter workspace'}).click();
}

test('three roles use the existing retail design; Distributor has chat only', async ({page}, info) => {
  const state = await setup(page);
  await expect(page.getByRole('radio')).toHaveCount(3);
  await page.screenshot({path:info.outputPath('three-role-login.png'),fullPage:true});
  await enter(page);
  await expect(page.getByRole('heading',{name:'Distributor agent',exact:true})).toBeVisible();
  await expect(page.getByRole('navigation',{name:'Main navigation'}).getByRole('button')).toHaveText(['Agent Chat']);
  for (const name of ['Overview','Request Activity','Evaluation & metrics','Agent registry','View requests','Question history']) {
    await expect(page.getByRole('button',{name,exact:true})).toHaveCount(0);
  }
  expect(state.calls.find(c=>c.path==='/sessions').body).toEqual({role:'distributor'});
  expect(state.calls.some(c=>/inventory|\/cases|\/chat\/|\/registry|activity/.test(c.path))).toBe(false);
  await expect(page.locator('.topbar')).toHaveCSS('background-color','rgb(49, 45, 42)');
  expect(await page.evaluate(()=>getComputedStyle(document.documentElement).getPropertyValue('--brand').trim())).toBe('#c74634');
  await page.screenshot({path:info.outputPath('distributor-desktop.png'),fullPage:true});
  expect(state.errors).toEqual([]);
});

test('sends a real API-shaped question, shows sources and preserves session chat', async ({page}, info) => {
  const state = await setup(page);
  await enter(page);
  const input = page.getByLabel('Message to distributor');
  await input.fill('Compare delivery');
  await input.press('Enter');
  await expect(page.getByRole('log')).toContainText(answer.answer);
  await page.getByText('Sources (1)',{exact:true}).click();
  await expect(page.getByText(answer.sources[0].excerpt,{exact:true})).toBeVisible();
  expect(state.calls.filter(c=>c.path==='/distributor/questions/ask').map(c=>c.body)).toEqual([{question:'Compare delivery'}]);
  await page.screenshot({path:info.outputPath('distributor-answer.png'),fullPage:true});
  await page.reload();
  await expect(page.getByRole('log')).toContainText(answer.answer);
  expect(state.calls.filter(c=>c.path==='/distributor/questions/ask')).toHaveLength(1);
  await page.getByRole('button',{name:'New chat',exact:true}).click();
  await expect(page.getByRole('log')).not.toContainText(answer.answer);
  await page.getByRole('button',{name:'Compare delivery',exact:true}).click();
  await expect(page.getByRole('log')).toContainText(answer.answer);
});

test('old-role hashes cannot mount business screens for Distributor', async ({page}) => {
  const state = await setup(page);
  await enter(page);
  for (const hash of ['overview','activity','evaluation','agent-registry','not-a-page']) {
    await page.evaluate(value=>{window.location.hash=value},hash);
    await expect(page.getByRole('heading',{name:'Distributor agent',exact:true})).toBeVisible();
  }
  expect(state.calls.some(c=>/inventory|\/cases|\/registry|\/chat\//.test(c.path))).toBe(false);
});

test('knowledge not ready disables questions and does not invent an answer', async ({page}) => {
  const state = await setup(page,{knowledge:{ready:false,message:'Knowledge is awaiting preparation.'}});
  await enter(page);
  await expect(page.getByText('Knowledge is awaiting preparation.')).toBeVisible();
  await expect(page.getByLabel('Message to distributor')).toBeDisabled();
  await expect(page.getByRole('button',{name:'Send message',exact:true})).toBeDisabled();
  expect(state.calls.some(c=>c.path.endsWith('/ask'))).toBe(false);
});

test('API failures preserve the question and never auto-retry a POST', async ({page}) => {
  const state = await setup(page,{answerStatus:503,answer:{detail:'Knowledge temporarily unavailable.'}});
  await enter(page);
  await page.getByLabel('Message to distributor').fill('My question');
  await page.getByRole('button',{name:'Send message',exact:true}).click();
  await expect(page.getByRole('alert')).toContainText('Knowledge temporarily unavailable.');
  await expect(page.getByLabel('Message to distributor')).toHaveValue('My question');
  await expect(page.getByRole('log')).toBeEmpty();
  expect(state.calls.filter(c=>c.path.endsWith('/ask'))).toHaveLength(1);
});

test('malformed responses and legacy backend login have clear errors', async ({page}) => {
  await setup(page,{loginFailure:true});
  await enter(page);
  await expect(page.getByRole('alert')).toContainText('Distributor chat needs the updated backend');
  await expect(page.getByRole('radio')).toHaveCount(3);
});

test('source and model content cannot execute HTML or load tracking images', async ({page}) => {
  await setup(page,{answer:{...answer,answer:'<script>window.injected=true</script>\n![track](https://invalid.example/pixel) Safe text [source-1]',
    sources:[{...answer.sources[0],excerpt:'<img src=x onerror="window.injected=true">'}]}});
  await enter(page);
  await page.getByLabel('Message to distributor').fill('Question');
  await page.getByRole('button',{name:'Send message',exact:true}).click();
  await expect(page.getByRole('log')).toContainText('Safe text');
  await expect(page.getByRole('log').locator('img,script')).toHaveCount(0);
  expect(await page.evaluate(()=>window.injected)).toBeUndefined();
});

test('mobile chat uses retail sidebar and composer without horizontal overflow', async ({page},info) => {
  await page.setViewportSize({width:390,height:844});
  await setup(page);
  await page.screenshot({path:info.outputPath('login-mobile.png'),fullPage:true});
  await enter(page);
  await expect(page.getByLabel('Message to distributor')).toBeInViewport();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({path:info.outputPath('distributor-mobile.png'),fullPage:true});
  await page.getByRole('button',{name:'Toggle navigation'}).click();
  await expect(page.getByRole('button',{name:'Agent Chat',exact:true})).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.locator('#workspace-sidebar')).not.toHaveClass(/open/);
});

test('login and Distributor chat pass automated accessibility checks', async ({page}) => {
  await setup(page);
  expect((await new AxeBuilder({page}).analyze()).violations).toEqual([]);
  await enter(page);
  await expect(page.getByLabel('Message to distributor')).toBeEnabled();
  expect((await new AxeBuilder({page}).analyze()).violations).toEqual([]);
});

test('sign-out clears Distributor chat instead of leaking it to the next role', async ({page}) => {
  await setup(page);
  await enter(page);
  await page.getByLabel('Message to distributor').fill('Private question');
  await page.getByRole('button',{name:'Send message',exact:true}).click();
  await expect(page.getByRole('log')).toContainText(answer.answer);
  await page.getByRole('button',{name:'Sign out',exact:true}).click();
  await expect(page.getByRole('radio',{name:/Retail manager/})).toBeVisible();
  expect(await page.evaluate(()=>Object.keys(sessionStorage).filter(key=>key.startsWith('retail.chats.')))).toEqual([]);
});
