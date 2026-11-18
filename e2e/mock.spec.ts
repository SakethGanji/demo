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

  // The simulated run still works end to end from here: target, goal, the race, the results.
  await page.getByRole('button', { name: 'Continue →' }).click()
  await expect(page.getByTestId('problem-type').locator('button.on')).toHaveText('binary')
  await page.getByRole('button', { name: 'Continue →' }).click()
  await page.getByRole('button', { name: 'Start Training' }).click()
  const race = page.getByTestId('race')
  await expect(race).toContainText('SIMULATED')
  await expect(race.getByTestId('race-model-LightGBMXT')).toBeVisible()
  await race.getByRole('button', { name: '3×' }).click()
  await expect(race).toHaveAttribute('data-status', 'succeeded', { timeout: 45_000 })
  await expect(page.getByTestId('race-equation')).toContainText('WeightedEnsemble_L2')
  await page.getByTestId('see-results').click()
  await expect(page.getByTestId('leaderboard')).toContainText('WeightedEnsemble_L2')
  await expect(page.getByTestId('confusion')).toContainText('bad')
  await expect(page.getByTestId('ce-fit')).toBeDisabled()
  await expect(page.getByTestId('custom-ensemble')).toContainText('Needs the live API')
  expect(errors).toEqual([])
})

test('experiments and explain in mock mode: the simulation is labelled, explain says it needs the API', async ({ page, errors }) => {
  await open(page, 'experiments')
  await expect(page.getByTestId('exp-simulated')).toContainText('SIMULATED')
  await expect(page.getByTestId('experiments-live')).toHaveCount(0)
  await open(page, 'explain')
  await expect(page.getByTestId('explain-mock')).toContainText('mock mode')
  await expect(page.locator('.statusbar')).toContainText('simulated')
  expect(errors).toEqual([])
})
