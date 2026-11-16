import { test, expect } from '@playwright/test';

test('actual browser to FastAPI to SQLite and local Ollama (explicit opt-in)', async ({ page }) => {
  test.skip(process.env.RAG_REAL_E2E !== '1', 'Requires running backend, index, and explicit actual-inference opt-in');
  test.setTimeout(200_000);
  await page.goto('/');
  await page.getByLabel('Your question').fill('How do I factory reset LumaDock Home?');
  const response = page.waitForResponse(r => r.url().endsWith('/api/ask') && r.request().method() === 'POST');
  await page.getByRole('button', { name: /Find an answer/ }).click();
  const actual = await response;
  expect(actual.status()).toBe(200);
  const body = await actual.json();
  expect(body.diagnostics.generation_called).toBe(true);
  expect(body.abstained).toBe(false);
  expect(body.citations.some((c: { quote: string }) => c.quote.includes('10 seconds'))).toBe(true);
  await expect(page.getByText('SOURCE VERIFIED')).toBeVisible();
  await page.screenshot({ path: 'test-results/actual-local-desktop.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: 'test-results/actual-local-mobile.png', fullPage: true });
});
