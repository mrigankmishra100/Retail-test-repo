import { test, expect } from '@playwright/test';

const endpoint = 'https://inference.generativeai.ap-hyderabad-1.oci.oraclecloud.com/20251112/hostedApplications/ocid1.generativeaihostedapplication.oc1.ap-hyderabad-1.example/actions/invoke';

async function workspace(page, role, { unavailable = false, conflict = false } = {}) {
  const session = {
    session_id: '11111111-1111-4111-8111-111111111111', role,
    supplier_id: role === 'supplier' ? 'SUP001' : null,
    session_token: 'x'.repeat(43), expires_at: '2099-01-01T00:00:00Z',
  };
  const agents = [];
  const requests = [];
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
      if (unavailable) return route.fulfill({ status: 503, json: { error: { message: 'Unavailable' } } });
      if (request.method() === 'GET') return route.fulfill({ json: { items: agents, has_more: false } });
      if (conflict) return route.fulfill({ status: 409, json: { error: { message: 'Conflict' } } });
      if (path === '/registry/agents') {
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

for (const role of ['retail-manager', 'supplier']) {
  test(`${role} can register, activate and deactivate an agent`, async ({ page }, testInfo) => {
    const { agents, requests } = await workspace(page, role);
    await expect(page.getByText('No registered agents')).toBeVisible();
    await page.getByLabel('Name', { exact: true }).fill('Demo agent');
    await page.getByLabel('Role / type').selectOption('CUSTOM');
    await page.getByLabel('Description', { exact: true }).fill('Stored for a later integration');
    await page.getByLabel('Public OCI Hosted Application URL').fill(endpoint);
    await page.getByRole('button', { name: 'Register agent', exact: true }).click();
    await expect(page.getByRole('status').filter({ hasText: 'Demo agent registered.' })).toBeVisible();
    await expect(page.getByRole('cell', { name: endpoint, exact: true })).toBeVisible();
    await expect(page.getByText('Stored only', { exact: true })).toBeVisible();
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
  await page.getByLabel('Public OCI Hosted Application URL').fill('https://example.com/agent');
  await page.getByRole('button', { name: 'Register agent', exact: true }).click();
  expect(requests.some((request) => request.method === 'POST')).toBe(false);
  expect(await page.getByLabel('Public OCI Hosted Application URL').evaluate((input) => input.validity.valid)).toBe(false);
});

test('missing migration shows a useful error', async ({ page }) => {
  await workspace(page, 'supplier', { unavailable: true });
  await expect(page.getByRole('alert')).toContainText('migration 004');
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
