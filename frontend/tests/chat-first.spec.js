import { test, expect } from '@playwright/test';

const id = '22222222-2222-4222-8222-222222222222';
const hash = 'a'.repeat(64);
const quote = { supplier_id: 'SUP001', supplier_name: 'Fresh Supply', item_id: 'ITEM001', quantity: 100, unit: 'units', priced_total: 1000, unit_price: 10, available_quantity: 450, feasible: true, lead_time_days: 2, payment_terms: 'Net 30', delivery_terms: 'Standard delivery', exclusions: [], warnings: [], policy_source: 'object_storage' };
const email = { subject: 'Replenishment request', body: 'Please supply 100 units of ITEM001 for INR 1000.', delivery_mode: 'case_update_topic' };

async function setup(page, role = 'retail-manager', options = {}) {
  const session = { session_id: '11111111-1111-4111-8111-111111111111', session_token: 'test-session', role, supplier_id: role === 'supplier' ? 'SUP001' : null };
  const state = {
    calls: [], starts: [],
    record: { case_id: id, item_id: 'ITEM001', current_stock: 10, recommended_quantity: 100, selected_supplier_id: 'SUP001', version: 1, draft_hash: hash,
      status: role === 'supplier' ? 'AWAITING_SUPPLIER_APPROVAL' : 'AWAITING_MANAGER_APPROVAL', draft: { quote, supplier_response: email }, notifications: [], decisions: [] },
    workflow: { thread_id: 'retail-thread', status: 'awaiting_selection', pending_input: 'selection', message: 'Stock is at risk. Review feasible suppliers.', can_resume: false,
      comparison: { item_id: 'ITEM001', quantity: 100, recommended_supplier_id: 'SUP001', suppliers: [quote] } },
    supplierStarted: false,
  };
  await page.addInitScript((saved) => sessionStorage.setItem('retail.session.v1', JSON.stringify(saved)), session);
  await page.route('**/api/**', async (route) => {
    const req = route.request(), path = new URL(req.url()).pathname.replace(/^\/api/, '');
    state.calls.push({ path, method: req.method(), body: req.postDataJSON(), key: req.headers()['idempotency-key'] });
    const reply = (data, status = 200) => route.fulfill({ status, json: data });
    if (path === '/sessions/me') return reply(session);
    if (path === '/health') return reply({ status: 'ok' });
    if (path === '/inventory/risks') return reply({ summary: { inventory_item_count: 5, at_risk_count: 1, not_at_risk_count: 4, missing_history_count: 0 }, rules: {}, at_risk_items: [{ item_id: 'ITEM001', item_name: 'Ice Cream', current_stock: 10, status: 'at_risk', days_of_supply: 1, recommended_quantity: 100 }], not_at_risk_items: [], unassessed_items: [] });
    if (path === '/cases') return reply({ items: options.empty ? [] : [state.record], has_more: false });
    if (path === `/cases/${id}`) return reply(state.record);
    if (path === `/cases/${id}/supplier-email`) return reply({ case_id: id, version: options.stalePreview ? 0 : state.record.version, draft_hash: hash, email });
    if (path === '/retail/workflows' && req.method() === 'POST') {
      state.starts.push(req.headers()['idempotency-key']);
      if (options.failStart && state.starts.length === 1) return reply({ error: { message: 'Temporary service unavailable', trace_id: 'test-trace' } }, 503);
      return reply(state.workflow);
    }
    if (path === '/retail/workflows/retail-thread') return reply(state.workflow);
    if (path === '/retail/workflows/retail-thread/selection') {
      state.workflow = { ...state.workflow, comparison: null, case: state.record, status: 'awaiting_approval', pending_input: 'manager_approval', message: 'Your draft is ready for review.' };
      return reply(state.workflow);
    }
    if (path.endsWith('-decision')) {
      if (options.conflict) return reply({ error: { message: 'Draft changed. Refresh and review.', trace_id: 'conflict-trace' } }, 409);
      const body = req.postDataJSON();
      expect(body.version).toBe(state.record.version);
      expect(body.draft_hash).toBe(hash);
      expect(req.headers()['idempotency-key']).toBeTruthy();
      state.record = { ...state.record, version: state.record.version + 1,
        status: body.approved ? (role === 'supplier' ? 'RESPONSE_QUEUED' : 'REQUEST_QUEUED') : (role === 'supplier' ? 'SUPPLIER_REJECTED' : 'MANAGER_REJECTED') };
      return reply(state.record);
    }
    if (path.endsWith('/dispatch/request')) {
      expect(state.record.status).toBe('REQUEST_QUEUED');
      state.record = { ...state.record, status: 'AWAITING_SUPPLIER_APPROVAL', notifications: [{ type: 'SUPPLIER_REQUEST', status: 'SENT' }] };
      return reply(state.record);
    }
    if (path === '/supplier/workflows') {
      state.supplierStarted = true;
      return reply({ thread_id: 'supplier-thread', status: 'awaiting_approval', case: state.record });
    }
    if (path === '/supplier/workflows/supplier-thread') return reply({ thread_id: 'supplier-thread', status: state.record.status === 'RESPONSE_QUEUED' ? 'ready' : 'awaiting_approval', can_resume: state.record.status === 'RESPONSE_QUEUED', pending_input: 'approval', message: 'Availability and policy checked.', inventory: { available_quantity: 450 }, policy: { content: 'Net 30. Standard delivery.' }, quote, case: state.record });
    if (path === '/supplier/workflows/supplier-thread/resume') {
      expect(state.record.status).toBe('RESPONSE_QUEUED');
      state.record = { ...state.record, status: 'COMPLETED', notifications: [{ type: 'SUPPLIER_RESPONSE', status: 'SENT' }] };
      return reply({ thread_id: 'supplier-thread', status: 'completed', case: state.record });
    }
    if (path === '/chat') return reply({ message: 'ITEM001 needs review based on current demand.', mode: 'llm_read_only', used_tools: ['get_inventory_risk'], conversation_id: 'conversation-1' });
    return reply({ error: { message: `Unmocked route: ${path}` } }, 404);
  });
  await page.goto('/');
  return state;
}

for (const role of ['retail-manager', 'supplier']) {
  test(`${role}: full-width overview, animated bot and chat welcome`, async ({ page }, info) => {
    const state = await setup(page, role);
    const grid = page.locator('.overview-primary');
    const panel = grid.locator('.panel');
    await expect(panel).toBeVisible();
    const bounds = await grid.boundingBox(), child = await panel.boundingBox();
    expect(Math.abs(bounds.width - child.width)).toBeLessThan(2);
    await expect(page.locator('.bot-orbit')).toHaveCSS('animation-name', 'bot-hello');
    await page.screenshot({ path: info.outputPath('overview.png'), fullPage: true });
    const bot = page.getByRole('button', { name: 'Open Agent Chat', exact: true });
    await bot.focus();
    await page.keyboard.press('Enter');
    await expect(page.locator('.welcome-message')).toContainText(role === 'supplier' ? 'Hi! Let’s review your incoming requests.' : 'Hi! Let’s keep your shelves stocked.');
    await expect(page.locator('.welcome-message')).toBeInViewport();
    expect(state.calls.some((c) => c.method === 'POST')).toBe(false);
    await page.screenshot({ path: info.outputPath('chat-welcome.png'), fullPage: true });
    await page.getByRole('button', { name: 'Overview', exact: true }).click();
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await expect(page.locator('.bot-orbit')).toHaveCSS('animation-name', 'none');
  });

  test(`${role}: three tabs, no overview shortcuts, activity is read-only`, async ({ page }) => {
    const state = await setup(page, role);
    await expect(page.getByRole('navigation').getByRole('button')).toHaveText(['Overview', 'Agent Chat', 'Request Activity']);
    await expect(page.getByRole('button', { name: 'Start replenishment', exact: true })).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Ask your assistant', exact: true })).toHaveCount(0);
    await page.getByRole('button', { name: 'Request Activity', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Requests and responses' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Approve draft', exact: true })).toHaveCount(0);
    expect(state.calls.some((c) => c.method === 'POST')).toBe(false);
    await page.getByRole('button', { name: 'Open in chat', exact: true }).click();
    await expect(page.getByRole('region', { name: 'Request review in chat' })).toBeVisible();
  });
}

test('retail: assessment, supplier selection, exact-draft approval and dispatch stay in chat', async ({ page }, info) => {
  const state = await setup(page);
  await page.getByRole('button', { name: 'Agent Chat', exact: true }).click();
  await page.getByRole('button', { name: 'Start a replenishment assessment', exact: true }).click();
  expect(state.starts).toHaveLength(0);
  await page.getByRole('button', { name: 'Ask agent to assess', exact: true }).click();
  await page.getByRole('button', { name: 'Select supplier', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Approve draft', exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: 'Review draft and terms', exact: true }).click();
  const approve = page.getByRole('button', { name: 'Approve draft', exact: true });
  await expect(approve).toBeDisabled();
  await page.getByLabel('I have reviewed this draft, its supplier email, and its commercial terms.').check();
  await expect(approve).toBeEnabled();
  await page.screenshot({ path: info.outputPath('retail-review.png'), fullPage: true });
  await approve.click();
  const dispatch = page.getByRole('button', { name: 'Dispatch approved email', exact: true });
  await expect(dispatch).toBeVisible();
  expect(state.calls.filter((c) => c.path.includes('/dispatch/'))).toHaveLength(0);
  await dispatch.click();
  await expect(page.getByRole('region', { name: 'Request review in chat' })).toContainText('Awaiting supplier approval');
  await expect(page.getByRole('heading', { name: 'Retail Agent Chat' })).toBeVisible();
  await page.reload();
  await page.getByRole('button', { name: 'Agent Chat', exact: true }).click();
  await expect(page.getByRole('region', { name: 'Request review in chat' })).toHaveCount(0);
  await page.getByRole('button', { name: /Continue saved request/ }).click();
  await expect(page.getByRole('region', { name: 'Request review in chat' })).toContainText('Awaiting supplier approval');
});

test('supplier: checks, approval and response completion stay in chat', async ({ page }, info) => {
  const state = await setup(page, 'supplier');
  await page.getByRole('button', { name: 'Agent Chat', exact: true }).click();
  await page.getByRole('button', { name: 'Review my incoming requests', exact: true }).click();
  await page.getByRole('button', { name: 'Review request', exact: true }).click();
  await page.getByRole('button', { name: 'Start supplier review', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Approve draft', exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: 'Review response and terms', exact: true }).click();
  await page.getByRole('checkbox').check();
  await page.getByRole('button', { name: 'Approve draft', exact: true }).click();
  await page.getByRole('button', { name: 'Send approved response & complete', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Approved request summary' }).first()).toBeVisible();
  expect(state.record.status).toBe('COMPLETED');
  await expect(page.getByRole('heading', { name: 'Supplier Agent Chat' })).toBeVisible();
  await page.screenshot({ path: info.outputPath('supplier-completed.png'), fullPage: true });
});

test('plain approval text never records a decision', async ({ page }) => {
  const state = await setup(page);
  await page.getByRole('button', { name: 'Agent Chat', exact: true }).click();
  await page.getByLabel('Message to agent').fill('Approve and send all requests');
  await page.getByRole('button', { name: 'Send message', exact: true }).click();
  await expect(page.getByRole('log')).toContainText('A chat message alone never approves');
  expect(state.calls.some((c) => c.method === 'POST')).toBe(false);
});

test('stale email preview blocks approval', async ({ page }) => {
  await setup(page, 'retail-manager', { stalePreview: true });
  await page.locator('.lower-grid').getByRole('button', { name: 'Review in chat', exact: true }).click();
  await page.getByRole('button', { name: 'Review draft and terms', exact: true }).click();
  await page.getByRole('checkbox').check();
  await expect(page.getByRole('button', { name: 'Approve draft', exact: true })).toBeDisabled();
  await expect(page.getByText('The email snapshot no longer matches this case version. Refresh before approving.')).toBeVisible();
});

test('conflict requires a fresh review', async ({ page }) => {
  await setup(page, 'retail-manager', { conflict: true });
  await page.locator('.lower-grid').getByRole('button', { name: 'Review in chat', exact: true }).click();
  await page.getByRole('button', { name: 'Review draft and terms', exact: true }).click();
  await page.getByRole('checkbox').check();
  await page.getByRole('button', { name: 'Approve draft', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Approve draft', exact: true })).toBeDisabled();
  await expect(page.getByRole('alert')).toContainText('conflict-trace');
});

test('assessment retry after refresh retains the idempotency key', async ({ page }) => {
  const state = await setup(page, 'retail-manager', { failStart: true });
  async function start() {
    await page.getByRole('button', { name: 'Agent Chat', exact: true }).click();
    await page.getByRole('button', { name: 'Start a replenishment assessment', exact: true }).click();
    await page.getByRole('button', { name: 'Ask agent to assess', exact: true }).click();
  }
  await start();
  await expect(page.getByRole('alert')).toContainText('test-trace');
  await page.reload();
  await start();
  await expect(page.getByRole('button', { name: 'Select supplier', exact: true })).toBeVisible();
  expect(state.starts).toHaveLength(2);
  expect(state.starts[0]).toBe(state.starts[1]);
});

test('retail questions use the existing conversation endpoint', async ({ page }) => {
  const state = await setup(page);
  await page.getByRole('button', { name: 'Agent Chat', exact: true }).click();
  await page.getByLabel('Message to agent').fill('Which items are at risk today?');
  await page.getByRole('button', { name: 'Send message', exact: true }).click();
  await expect(page.getByRole('log')).toContainText('Model-assisted answer');
  expect(state.calls.find((c) => c.path === '/chat').body.intent).toBe('conversation');
});

test('mobile navigation and chat fit without horizontal overflow', async ({ page }, info) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await setup(page, 'supplier');
  await page.getByRole('button', { name: 'Toggle navigation' }).click();
  await page.getByRole('button', { name: 'Agent Chat', exact: true }).click();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath('chat-mobile.png'), fullPage: true });
});

for (const role of ['retail-manager', 'supplier']) {
  test(`${role}: greeting ignores saved request and uses a compact bubble`, async ({ page }, info) => {
    const state = await setup(page, role);
    await page.locator('.lower-grid').getByRole('button', { name: 'Review in chat', exact: true }).click();
    await page.getByLabel('Message to agent').fill('hi');
    await page.getByRole('button', { name: 'Send message', exact: true }).click();
    const last = page.getByRole('log').locator('.message').last();
    await expect(last).toContainText('Hi! How can I help?');
    await expect(last).not.toContainText('rejected');
    await expect(page.getByRole('region', { name: 'Request review in chat' })).toHaveCount(0);
    expect(state.calls.some((c) => c.method === 'POST')).toBe(false);
    const userBubble = await page.getByRole('log').locator('.message.user').last().boundingBox();
    expect(userBubble.width).toBeLessThan(200);
    await page.screenshot({ path: info.outputPath('greeting.png'), fullPage: true });
    await page.getByRole('button', { name: /Continue saved request/ }).click();
    await expect(page.getByRole('region', { name: 'Request review in chat' })).toBeVisible();
  });
}

test('workflow context is opt-in and new chat clears model context, not the saved request', async ({ page }, info) => {
  const state = await setup(page);
  await page.getByRole('button', { name: 'Agent Chat', exact: true }).click();
  await page.getByRole('button', { name: 'Start a replenishment assessment', exact: true }).click();
  await page.getByRole('button', { name: 'Ask agent to assess', exact: true }).click();
  await page.screenshot({ path: info.outputPath('supplier-options.png'), fullPage: true });
  await page.getByRole('button', { name: 'Select supplier', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Review draft and terms' })).toBeVisible();
  await page.screenshot({ path: info.outputPath('next-action.png'), fullPage: true });
  async function ask() {
    const count = state.calls.filter((c) => c.path === '/chat').length;
    await page.getByLabel('Message to agent').fill('What do these terms mean?');
    await page.getByRole('button', { name: 'Send message', exact: true }).click();
    await expect.poll(() => state.calls.filter((c) => c.path === '/chat').length).toBe(count + 1);
    await expect(page.getByLabel('Message to agent')).toBeEnabled();
    return state.calls.filter((c) => c.path === '/chat').at(-1).body;
  }
  expect((await ask()).workflow_thread_id).toBeUndefined();
  await page.getByLabel('Use this request as context for my questions').check();
  const scoped = await ask();
  expect(scoped.workflow_thread_id).toBe('retail-thread');
  expect(scoped.conversation_id).toBeUndefined();
  await page.getByRole('button', { name: 'New chat', exact: true }).click();
  const general = await ask();
  expect(general.workflow_thread_id).toBeUndefined();
  expect(general.conversation_id).toBeUndefined();
  await expect(page.getByRole('button', { name: /Continue saved request/ })).toBeVisible();
});
