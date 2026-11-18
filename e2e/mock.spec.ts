// Mock mode: the API is down, the status bar says so, and the UI keeps working on its built-in simulation.
import { apiMode, expect, open, test } from './fixtures'

test('status bar falls back to mock mode', async ({ page, errors }) => {
  await open(page, 'home')
  await expect(apiMode(page)).toHaveAttribute('data-mode', 'mock')
  await expect(apiMode(page)).toContainText('mock mode')
  expect(errors).toEqual([])
})

test('dataset step uses the built-in samples and the browser profiler', async ({ page, errors }) => {
  await open(page, 'quick-train')
  await expect(apiMode(page)).toHaveAttribute('data-mode', 'mock')
  const profile = page.getByTestId('dataset-profile')
  await expect(profile).toHaveAttribute('data-source', 'mock')
  await expect(page.getByTestId('dataset-list')).toContainText('Built-in · mock')
  await expect(page.getByTestId('profile-name')).toHaveText('zendesk_tickets_2026_q3.parquet')

  await page.getByTestId('dataset-list').getByText('account_churn_2026.csv').click()
  await expect(page.getByTestId('profile-name')).toHaveText('account_churn_2026.csv')

  const rows = ['id,message,label']
  for (let i = 0; i < 30; i++) rows.push(`${i},"the parcel arrived late and the box was crushed on arrival ${i}",${i % 3 === 0 ? 'bad' : 'good'}`)
  await page.getByTestId('dataset-file').setInputFiles({ name: 'offline.csv', mimeType: 'text/csv', buffer: Buffer.from(rows.join('\n')) })
  await expect(page.getByTestId('profile-name')).toHaveText('offline.csv')
  await expect(page.getByTestId('profile-source')).toContainText('profiled from the first rows')

  // The simulated run still works end to end from here.
  await page.getByRole('button', { name: 'Continue →' }).click()
  await page.getByRole('button', { name: 'Start Training' }).click()
  await expect(page.locator('.workspace')).toContainText('fold_1')
  expect(errors).toEqual([])
})
