// Poll an async check in the page until it returns something truthy.
//
// page.waitForFunction stops at the first truthy return value, and the
// Promise an async predicate returns is truthy, so
// `waitForFunction(async () => (await window.rom.backend.info()).status === 'running')`
// returns at once whatever the status is. Suites that used it passed only when
// the backend happened to be ready already, and failed as soon as the renderer
// started faster. This awaits each check from Node instead.
export async function waitUntil(page, check, {arg, timeout = 30000, interval = 100, message} = {}) {
  const deadline = Date.now() + timeout;
  for (;;) {
    if (await page.evaluate(check, arg)) return;
    if (Date.now() >= deadline) {
      throw new Error(message || `waitUntil: condition not met within ${timeout}ms`);
    }
    await page.waitForTimeout(interval);
  }
}

export const backendRunning = (page, timeout = 60000) => waitUntil(
  page,
  async () => (await window.rom.backend.info()).status === 'running',
  {timeout, message: `backend did not reach "running" within ${timeout}ms`},
);
