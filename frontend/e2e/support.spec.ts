import { test, expect } from '@playwright/test';

test('synthetic answer, citation, diagnostic details and safe text rendering', async ({ page }) => {
  await page.route('**/api/ask', route => route.fulfill({ json: {
    answer: 'Hold the reset button for 10 seconds. [reset:c000]', abstained: false,
    citations: [{ chunk_id: 'reset:c000', title: 'Factory Reset Procedure', version: '2.3', quote: 'Hold the reset button for 10 seconds.', normalized_start: 100, normalized_end: 142 }],
    passages: [{ id: 'reset:c000', title: 'Factory Reset Procedure', version: '2.3', text: '<script>window.compromised = true</script>', similarity: .82, semantic_rank: 1, keyword_rank: 1, rrf_score: 2 / 61, source_start: 100, source_end: 400, normalized_start: 100, normalized_end: 400 }],
    diagnostics: { reason: 'verified_exact_quotes', algorithm: 'RRF(k=60)', generation_called: true, embedding_model: 'nomic-embed-text', generation_model: 'qwen3:4b' },
  } }));
  await page.goto('/');
  await page.getByRole('button', { name: /factory reset/ }).click();
  await page.getByRole('button', { name: /Find an answer/ }).click();
  await expect(page.getByText('SOURCE VERIFIED')).toBeVisible();
  await expect(page.getByText('Hold the reset button for 10 seconds.', { exact: true })).toBeVisible();
  await page.getByText('Retrieval diagnostics').click();
  await expect(page.getByText('<script>window.compromised = true</script>')).toBeVisible();
  expect(await page.evaluate(() => 'compromised' in window)).toBe(false);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test('synthetic unsupported answer is explicit', async ({ page }) => {
  await page.route('**/api/ask', route => route.fulfill({ json: { answer: 'I cannot answer that from the available documentation.', abstained: true, citations: [], passages: [], diagnostics: { reason: 'insufficient_evidence', algorithm: 'RRF(k=60)', generation_called: false } } }));
  await page.goto('/');
  await page.getByLabel('Your question').fill('What is the price?');
  await page.getByRole('button', { name: /Find an answer/ }).click();
  await expect(page.getByText('ABSTAINED')).toBeVisible();
  await expect(page.getByText('I cannot answer that from the available documentation.')).toBeVisible();
});
