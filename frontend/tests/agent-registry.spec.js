import { test, expect } from '@playwright/test';

const endpoint = 'https://inference.generativeai.ap-hyderabad-1.oci.oraclecloud.com/20251112/hostedApplications/ocid1.generativeaihostedapplication.oc1.ap-hyderabad-1.example/actions/invoke';

async function workspace(page, role, { unavailable = false, conflict = false, connectionFailure = false, deleteConflict = false, expectedToken = 'fixture-delivery-token-123456' } = {}) {
  const session = {
    session_id: '11111111-1111-4111-8111-111111111111', role,
    supplier_id: role === 'supplier' ? 'SUP001' : null,
    session_token: 'x'.repeat(43), expires_at: '2099-01-01T00:00:00Z',
  };
  const agents = [];
  const requests = [];
  let connectedAgent = null;
  const connectionStatus = () => ({ agent_id: connectedAgent, connected: !!connectedAgent,
    available: !!connectedAgent && !!agents.find(a => a.agent_id === connectedAgent)?.active,
    state: connectedAgent ? 'ready' : 'not_connected', expires_at: connectedAgent ? session.expires_at : null });
  await page.addInitScript((saved) => sessionStorage.setItem('retail.session.v1', JSON.stringify(saved)), session);
  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace(/^\/api/, '');
    if (path === '/sessions/me') return route.fulfill({ json: session });
    if (path === '/health') return route.fulfill({ json: { status: 'ok' } });
    if (path === '/cases') return route.fulfill({ json: { items: [], has_more: false } });
    if (path === '/inventory/risks') return route.fulfill({ json: { summary: {}, rules: {}, items: [], at_risk_items: [] } });
    if (path.startsWith('/registry/agents')) {
      expect(request.headers().authorization).toBe(`Bearer ${session.session_token}`);
      requests.push({ method: request.method(), path });
      if (path.endsWith('/delivery-connection')) {
        if (request.method() === 'POST') {
          expect(request.postDataJSON()).toEqual({ api_token: expectedToken });
          if (connectionFailure) return route.fulfill({ status: 503, json: { error: { message: 'Save failed' } } });
          connectedAgent = path.split('/')[3];
        } else if (request.method() === 'DELETE') connectedAgent = null;
        return route.fulfill({ json: connectionStatus() });
      }
      if (unavailable) return route.fulfill({ status: 503, json: { error: { message: 'Unavailable' } } });
      if (request.method() === 'GET') return route.fulfill({ json: { items: agents, has_more: false, enforcement_enabled: true } });
      if (request.method() === 'DELETE') {
        const index = agents.findIndex(a => a.agent_id === path.split('/')[3]);
        if (index < 0) return route.fulfill({ status: 404, json: { error: { message: 'Not found' } } });
        if (deleteConflict || agents[index].active) return route.fulfill({ status: 409, json: { error: { message: 'Conflict' } } });
        const [removed] = agents.splice(index, 1);
        return route.fulfill({ json: { agent_id: removed.agent_id, deleted: true } });
      }
      if (conflict) return route.fulfill({ status: 409, json: { error: { message: 'Conflict' } } });
      if (path === '/registry/agents') {
        expect(Object.keys(request.postDataJSON()).sort()).toEqual(['active','agent_type','description','endpoint_url','name']);
        const agent = { ...request.postDataJSON(), agent_id: '22222222-2222-4222-8222-222222222222',
          created_at: '2026-09-18T00:00:00Z', updated_at: '2026-09-18T00:00:00Z' };
        agents.push(agent);
        return route.fulfill({ status: 201, json: agent });
      }
      agents[0].active = path.endsWith('/activate');
      return route.fulfill({ json: agents[0] });
    }
    return route.fulfill({ status: 404, json: { error: { message: 'Unmocked route' } } });
  });
  await page.goto('/');
  await page.getByRole('button', { name: 'Agent registry', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Agent registry', exact: true })).toBeVisible();
  return { agents, requests };
}

test('supplier registers REST delivery agent and connects a session token', async ({ page }, testInfo) => {
  const { agents, requests } = await workspace(page, 'supplier');
  await page.getByLabel('Name', { exact: true }).fill('Delivery agent');
  await expect(page.getByLabel('Role / type')).toHaveValue('CUSTOM');
  await expect(page.getByRole('option', { name: 'Delivery agent (REST)' })).toHaveCount(0);
  await page.getByLabel('Custom agent endpoint').fill('https://150.230.130.251:8081/aws-app/api/v1/answers');
  await page.getByRole('combobox', { name: 'Status', exact: true }).selectOption('true');
  await page.getByRole('button', { name: 'Register agent', exact: true }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Delivery agent registered.' })).toBeVisible();
  await page.getByText('Connect delivery API', { exact: true }).click();
  await page.getByLabel('Delivery API token').fill('fixture-delivery-token-123456');
  await page.getByRole('button', { name: 'Connect for this session', exact: true }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Delivery connection saved' })).toBeVisible();
  await expect(page.getByLabel('Delivery API token')).toHaveValue('');
  await expect(page.getByRole('status').filter({ hasText: /^Connected for this session$/ })).toBeVisible();
  expect(agents[0].agent_type).toBe('CUSTOM');
  expect(agents[0]).not.toHaveProperty('api_token');
  expect(await page.evaluate(() => JSON.stringify([localStorage, sessionStorage]))).not.toContain('fixture-delivery-token');
  expect(await page.getByRole('status').filter({ hasText: /^Connected for this session$/ }).evaluate(el => {
    const sidebar = document.querySelector('.sidebar').getBoundingClientRect();
    return el.getBoundingClientRect().left >= sidebar.right;
  })).toBe(true);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: testInfo.outputPath('delivery-connected.png') });
  await page.reload();
  await expect(page.getByRole('status').filter({ hasText: /^Connected for this session$/ })).toBeVisible();
  await page.getByText('Connect delivery API', { exact: true }).click();
  await expect(page.getByLabel('Delivery API token')).toHaveValue('');
  await page.getByRole('button', { name: 'Disconnect', exact: true }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Delivery connection removed' })).toBeVisible();
  await expect(page.getByRole('status').filter({ hasText: /^Not connected for this session$/ })).toBeVisible();
  expect(requests.filter(r => r.path.endsWith('/delivery-connection') && r.method !== 'GET').map(r => r.method)).toEqual(['POST','DELETE']);
});

for (const token of ['fixture-delivery-token-123456', '']) {
  test(`registration connects delivery in one submit ${token ? 'with bearer' : 'without authentication'}`, async ({ page }, testInfo) => {
    const { agents, requests } = await workspace(page, 'supplier', { expectedToken: token });
    await page.getByLabel('Name', { exact: true }).fill('Delivery agent');
    await page.getByLabel('Custom agent endpoint').fill('https://delivery.example.com/api/v1/answers');
    await page.getByRole('combobox', { name: 'Status', exact: true }).selectOption('true');
    await page.getByLabel('Use for delivery questions in this session').check();
    await page.getByLabel('Bearer token (optional)', { exact: true }).fill(token);
    await page.screenshot({ path: testInfo.outputPath('registration-connection.png') });
    await page.getByRole('button', { name: 'Register agent', exact: true }).click();
    await expect(page.getByRole('status').filter({ hasText: /^Connected for this session$/ })).toBeVisible();
    expect(agents).toHaveLength(1);
    expect(requests.filter(r => r.method === 'POST').map(r => r.path)).toEqual([
      '/registry/agents', '/registry/agents/22222222-2222-4222-8222-222222222222/delivery-connection']);
    expect(JSON.stringify(agents)).not.toContain('fixture-delivery-token');
    expect(await page.evaluate(() => JSON.stringify([localStorage, sessionStorage]))).not.toContain('fixture-delivery-token');
    await page.reload();
    await expect(page.getByRole('status').filter({ hasText: /^Connected for this session$/ })).toBeVisible();
  });
}

test('registration connection failure preserves the entry and explains how to retry', async ({ page }) => {
  const { agents, requests } = await workspace(page, 'supplier', { connectionFailure: true });
  await page.getByLabel('Name', { exact: true }).fill('Delivery agent');
  await page.getByLabel('Custom agent endpoint').fill('https://delivery.example.com/api/v1/answers');
  await page.getByLabel('Use for delivery questions in this session').check();
  await page.getByLabel('Bearer token (optional)', { exact: true }).fill('fixture-delivery-token-123456');
  await page.getByRole('button', { name: 'Register agent', exact: true }).click();
  await expect(page.getByRole('status').filter({ hasText: 'do not register it again' })).toBeVisible();
  await expect(page.getByRole('alert')).toContainText('agent was registered, but connecting it failed');
  expect(agents).toHaveLength(1);
  expect(requests.filter(r => r.method === 'POST' && r.path === '/registry/agents')).toHaveLength(1);
  await expect(page.getByLabel('Name', { exact: true })).toHaveValue('');
});

test('changing agent type clears registration credentials and does not connect a core role', async ({ page }) => {
  const { requests } = await workspace(page, 'supplier');
  await page.getByLabel('Use for delivery questions in this session').check();
  await page.getByLabel('Bearer token (optional)', { exact: true }).fill('fixture-delivery-token-123456');
  await page.getByLabel('Role / type').selectOption('RETAIL');
  await expect(page.getByLabel('Bearer token (optional)', { exact: true })).toHaveCount(0);
  await page.getByLabel('Name', { exact: true }).fill('Retail');
  await page.getByLabel('Public OCI Hosted Application URL').fill(endpoint);
  await page.getByRole('button', { name: 'Register agent', exact: true }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Retail registered.' })).toBeVisible();
  expect(requests.filter(r => r.method === 'POST').map(r => r.path)).toEqual(['/registry/agents']);
});

test('failed connection shows error beside token and never claims it is connected', async ({ page }) => {
  const { agents } = await workspace(page, 'supplier', { connectionFailure: true });
  agents.push({ agent_id: '22222222-2222-4222-8222-222222222222', name: 'Distributor', agent_type: 'CUSTOM',
    endpoint_url: 'https://delivery.example.com/api/v1/answers', active: true });
  await page.getByRole('button', { name: 'Refresh', exact: true }).click();
  await page.getByText('Connect delivery API', { exact: true }).click();
  await page.getByLabel('Delivery API token').fill('fixture-delivery-token-123456');
  await page.getByRole('button', { name: 'Connect for this session', exact: true }).click();
  const details = page.locator('details').filter({ has: page.getByLabel('Delivery API token') });
  await expect(details.getByRole('alert')).toContainText('Connection could not be updated');
  await expect(page.getByRole('status').filter({ hasText: /^Not connected for this session$/ })).toBeVisible();
  await expect(page.getByRole('status').filter({ hasText: /^Connected for this session$/ })).toHaveCount(0);
});

test('delete requires deactivation and confirmation and removes only the selected entry', async ({ page }) => {
  const { agents, requests } = await workspace(page, 'supplier');
  agents.push({ agent_id: '22222222-2222-4222-8222-222222222222', name: 'Distributor', agent_type: 'CUSTOM',
    endpoint_url: 'https://delivery.example.com/api/v1/answers', active: true });
  await page.getByRole('button', { name: 'Refresh', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Delete Distributor', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: 'Deactivate Distributor', exact: true }).click();
  await page.getByRole('button', { name: 'Delete Distributor', exact: true }).click();
  await expect(page.getByRole('dialog')).toContainText('does not delete the hosted application');
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  expect(requests.filter(r => r.method === 'DELETE')).toHaveLength(0);
  await page.getByRole('button', { name: 'Delete Distributor', exact: true }).click();
  await page.getByRole('button', { name: 'Delete registration', exact: true }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page.getByText('No registered agents')).toBeVisible();
  await expect(page.getByRole('status').filter({ hasText: 'Distributor removed from the registry.' })).toBeVisible();
  expect(requests.filter(r => r.method === 'DELETE').map(r => r.path)).toEqual(['/registry/agents/22222222-2222-4222-8222-222222222222']);
});

test('delete conflict remains in confirmation with useful guidance', async ({ page }) => {
  const { agents } = await workspace(page, 'retail-manager', { deleteConflict: true });
  agents.push({ agent_id: '22222222-2222-4222-8222-222222222222', name: 'Retail Agent', agent_type: 'RETAIL',
    endpoint_url: endpoint, active: false });
  await page.getByRole('button', { name: 'Refresh', exact: true }).click();
  await page.getByRole('button', { name: 'Delete Retail Agent', exact: true }).click();
  await expect(page.getByRole('dialog')).toContainText('bootstrap may register this role again');
  await page.getByRole('button', { name: 'Delete registration', exact: true }).click();
  await expect(page.getByRole('dialog').getByRole('alert')).toContainText('deactivate it before deleting');
  expect(agents).toHaveLength(1);
});

for (const role of ['retail-manager', 'supplier']) {
  test(`${role} can register, activate and deactivate an agent`, async ({ page }, testInfo) => {
    const { agents, requests } = await workspace(page, role);
    await expect(page.getByText('No registered agents')).toBeVisible();
    await page.getByText('Registration and routing', { exact: true }).click();
    await expect(page.locator('.registry-guidance')).toContainText('deactivating the last active Retail or Supplier entry blocks');
    await page.getByText('Registration and routing', { exact: true }).click();
    await page.getByLabel('Name', { exact: true }).fill('Demo agent');
    await page.getByLabel('Role / type').selectOption('CUSTOM');
    await page.getByText('Add a description', { exact: true }).click();
    await page.getByLabel('Description', { exact: true }).fill('Stored for a later integration');
    await page.getByLabel('Custom agent endpoint').fill(endpoint);
    await page.getByRole('button', { name: 'Register agent', exact: true }).click();
    await expect(page.getByRole('status').filter({ hasText: 'Demo agent registered.' })).toBeVisible();
    const details = page.getByLabel('Details for Demo agent', { exact: true });
    await expect(page.locator('.registry-agent-details')).not.toHaveAttribute('open');
    await details.click();
    await expect(page.locator('.registry-agent-details code')).toHaveText(endpoint);
    await details.click();
    await expect(page.getByText('Optional delivery connection', { exact: true })).toBeVisible();
    await page.getByText('Add a description', { exact: true }).click();
    const registration = await page.locator('.registry-registration').boundingBox();
    const directory = await page.locator('.registry-directory').boundingBox();
    expect(directory.y).toBeGreaterThanOrEqual(registration.y + registration.height);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
    await page.screenshot({ path: testInfo.outputPath('registry-desktop.png'), fullPage: true });
    await page.getByRole('button', { name: 'Activate Demo agent', exact: true }).click();
    await expect(page.getByRole('cell', { name: 'Active', exact: true })).toBeVisible();
    await page.getByRole('button', { name: 'Deactivate Demo agent', exact: true }).click();
    await expect(page.getByRole('cell', { name: 'Inactive', exact: true })).toBeVisible();
    expect(agents[0].active).toBe(false);
    expect(requests.filter((request) => request.method === 'POST').map((request) => request.path)).toEqual([
      '/registry/agents', '/registry/agents/22222222-2222-4222-8222-222222222222/activate',
      '/registry/agents/22222222-2222-4222-8222-222222222222/deactivate',
    ]);
    // The shell still exposes the existing role-specific workspace.
    await page.getByRole('button', { name: 'Overview', exact: true }).click();
    await expect(page.getByRole('heading', { name: role === 'supplier' ? 'Supplier overview' : 'Inventory overview', exact: true })).toBeVisible();
  });
}

test('invalid URLs stay in the form and are not submitted', async ({ page }) => {
  const { requests } = await workspace(page, 'retail-manager');
  await page.getByLabel('Name', { exact: true }).fill('Invalid URL');
  await page.getByLabel('Role / type').selectOption('RETAIL');
  await page.getByLabel('Public OCI Hosted Application URL').fill('https://example.com/agent');
  await page.getByRole('button', { name: 'Register agent', exact: true }).click();
  expect(requests.some((request) => request.method === 'POST')).toBe(false);
  expect(await page.getByLabel('Public OCI Hosted Application URL').evaluate((input) => input.validity.valid)).toBe(false);
});

test('missing migration shows a useful error', async ({ page }) => {
  await workspace(page, 'supplier', { unavailable: true });
  await expect(page.getByRole('alert')).toContainText('registry migrations (004');
  await expect(page.getByText('No registered agents')).toHaveCount(0);
});

test('activation conflict retains the form with registry-specific guidance', async ({ page }) => {
  await workspace(page, 'retail-manager', { conflict: true });
  await page.getByLabel('Name', { exact: true }).fill('New Retail agent');
  await page.getByLabel('Role / type').selectOption('RETAIL');
  await page.getByLabel('Public OCI Hosted Application URL').fill(endpoint);
  await page.getByRole('combobox', { name: 'Status', exact: true }).selectOption('true');
  await page.getByRole('button', { name: 'Register agent', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('Deactivate it before activating this agent');
  await expect(page.getByRole('alert')).not.toContainText('current case');
  await expect(page.getByLabel('Name', { exact: true })).toHaveValue('New Retail agent');
});

test('registry form fits a mobile viewport', async ({ page }, testInfo) => {
  await workspace(page, 'supplier');
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByLabel('Name', { exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath('registry-mobile.png'), fullPage: true });
});

test('wide registry stays vertical and long agent endpoints never push actions off screen', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1920, height: 1080 });
  const { agents } = await workspace(page, 'retail-manager');
  agents.push({ agent_id: '33333333-3333-4333-8333-333333333333', name: 'Retail Agent',
    agent_type: 'RETAIL', description: 'Retail worker', endpoint_url: endpoint.replace('example', 'x'.repeat(90)), active: true });
  await page.getByRole('button', { name: 'Refresh', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Deactivate Retail Agent', exact: true })).toBeVisible();
  const registration = await page.locator('.registry-registration').boundingBox();
  const directory = await page.locator('.registry-directory').boundingBox();
  expect(directory.y).toBeGreaterThanOrEqual(registration.y + registration.height);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  await page.getByLabel('Details for Retail Agent', { exact: true }).click();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath('registry-wide.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  await expect(page.getByRole('button', { name: 'Deactivate Retail Agent', exact: true })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('registry-agent-mobile.png'), fullPage: true });
});
