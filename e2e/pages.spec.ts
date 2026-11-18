// Every page opens and renders without console errors, in both live and mock mode (one project each).
import { ROUTES, apiMode, expect, open, test } from './fixtures'

const TITLES: Record<string, string> = {
  home: 'Home', 'quick-train': 'New Model', autopilot: 'Vela Autopilot', gym: 'Training Gym',
  experiments: 'Experiments', registry: 'Model Registry', settings: 'Settings',
}

for (const route of ROUTES) {
  test(`${route} renders without console errors`, async ({ page, errors }, info) => {
    await open(page, route)
    await expect(page).toHaveTitle(`Project ENSEMBLE · ${TITLES[route]}`)
    await expect(apiMode(page)).toHaveAttribute('data-mode', info.project.name)
    const text = await page.locator('.workspace').innerText()
    expect(text.trim().length).toBeGreaterThan(40)
    await page.waitForTimeout(500) // let effects and the first animation frames run
    expect(errors).toEqual([])
  })
}
