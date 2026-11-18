// Live mode: the API is up, the status bar says so, and Quick Train's dataset step profiles server data.
import { apiMode, expect, open, test } from './fixtures'

test('status bar reports live mode with the API version', async ({ page, errors }) => {
  await open(page, 'home')
  await expect(apiMode(page)).toHaveAttribute('data-mode', 'live')
  await expect(apiMode(page)).toContainText('live')
  const health = await (await page.request.get('/api/health')).json()
  await expect(apiMode(page)).toContainText(health.version)
  expect(errors).toEqual([])
})

test('dataset step profiles a server sample', async ({ page, errors }) => {
  await open(page, 'quick-train')
  const list = page.getByTestId('dataset-list')
  await expect(list.getByTestId('dataset-support-tickets')).toBeVisible()
  await expect(list.getByTestId('dataset-account-churn')).toBeVisible()

  // It starts on the first server sample, profiled by the API.
  const profile = page.getByTestId('dataset-profile')
  await expect(profile).toHaveAttribute('data-source', 'api')
  await expect(page.getByTestId('profile-name')).toHaveText('support_tickets_sample.csv')
  await expect(page.getByTestId('profile-rows')).toContainText('20,000')
  await expect(page.getByTestId('profile-k')).toContainText('6')
  await expect(profile).toContainText('Generated sample')
  await expect(profile).toContainText('Security')
  await expect(page.getByTestId('profile-source')).toContainText('Profiled by the API')
  await expect(page.getByTestId('profile-quality')).toContainText('holdout=4,000')
  await expect(page.getByTestId('profile-warnings')).toContainText('rare')

  // The numbers on screen are the API's numbers.
  const api = await (await page.request.get('/api/datasets/support-tickets')).json()
  await expect(page.getByTestId('profile-quality')).toContainText(`dup=${api.profile.duplicates.rows}`)
  for (const c of api.profile.classes) await expect(profile).toContainText(`${(c.share * 100).toFixed(1)}%`)

  // Switch to the churn sample.
  await list.getByTestId('dataset-account-churn').click()
  await expect(page.getByTestId('profile-name')).toHaveText('account_churn_sample.csv')
  await expect(page.getByTestId('profile-rows')).toContainText('10,000')
  await expect(page.getByTestId('profile-k')).toContainText('2')
  await expect(profile).toContainText('nps')
  await expect(page.getByTestId('profile-quality')).toContainText('missing=')
  await expect(page.locator('.valid')).toContainText('target churned · K = 2')
  expect(errors).toEqual([])
})

test('uploading a CSV profiles it on the server', async ({ page, errors }) => {
  await open(page, 'quick-train')
  await expect(page.getByTestId('dataset-profile')).toHaveAttribute('data-source', 'api')
  const rows = ['id,message,label']
  for (let i = 0; i < 40; i++) rows.push(`${i},"order ${i} arrived on time and the item works well enough for me",${i % 4 === 0 ? 'bad' : 'good'}`)
  await page.getByTestId('dataset-file').setInputFiles({ name: 'e2e_upload.csv', mimeType: 'text/csv', buffer: Buffer.from(rows.join('\n')) })
  await expect(page.getByTestId('profile-name')).toHaveText('e2e_upload.csv')
  await expect(page.getByTestId('profile-rows')).toContainText('40')
  await expect(page.getByTestId('profile-k')).toContainText('2')
  await expect(page.getByTestId('profile-source')).toContainText('Profiled by the API')
  await expect(page.locator('.valid')).toContainText('target label')
  const listed = await (await page.request.get('/api/datasets')).json()
  expect(listed.datasets.map((d: { filename: string }) => d.filename)).toContain('e2e_upload.csv')
  expect(errors).toEqual([])
})

test('a rejected upload shows the API message', async ({ page }) => {
  await open(page, 'quick-train')
  await expect(page.getByTestId('dataset-profile')).toHaveAttribute('data-source', 'api')
  await page.getByTestId('dataset-file').setInputFiles({ name: 'one_row.csv', mimeType: 'text/csv', buffer: Buffer.from('a,b\n1,2\n') })
  await expect(page.getByRole('alert')).toContainText('fewer than two data rows')
  await expect(page.getByTestId('profile-name')).toHaveText('support_tickets_sample.csv')
})
