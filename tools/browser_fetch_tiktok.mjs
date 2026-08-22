#!/usr/bin/env node
/**
 * TikTok feed URL signer.
 *
 * Opens ONE browser session, navigates through all category pages so the
 * web SDK signs each /webcast/feed/ request. At the routing layer we abort
 * the actual request but capture the fully-signed URL. Also exports all
 * session cookies needed by TikTok's API.
 *
 * Usage:
 *   node browser_fetch_tiktok.mjs <category_url1> [category_url2] ...
 *
 * Output (stdout): JSON {cookies: {...}, signedUrls: [...]}
 */
import { chromium } from 'patchright';

const UA =
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ' +
  '(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36';

const NAV_DELAY = 4000;
const RETRY_DELAY = 2000;

let browser;
try {
  browser = await chromium.launch({ channel: 'chrome', headless: true });
} catch {
  browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
}

try {
  const targets = process.argv.slice(2).filter(Boolean);
  if (!targets.length) throw new Error('need at least one category URL');

  const context = await browser.newContext({
    userAgent: UA,
    locale: 'en-US',
    viewport: { width: 1920, height: 1080 },
  });
  const page = await context.newPage();

  const signedUrls = [];

  // Intercept feed requests that have signatures, abort them,
  // and keep the signed URL for Python HTTP replay.
  await context.route('**/webcast.us.tiktok.com/webcast/feed/?**', async (route) => {
    const url = route.request().url();
    if (/X-Gnarly/.test(url)) {
      signedUrls.push(url);
      process.stderr.write(`[signer] captured signed URL (${signedUrls.length})\n`);
    }
    await route.abort();
  });

  for (let i = 0; i < targets.length; i++) {
    process.stderr.write(`[nav] (${i + 1}/${targets.length}) ${targets[i]}\n`);
    await page.goto(targets[i], {
      waitUntil: 'domcontentloaded', timeout: 30_000,
    }).catch(() => {});
    await page.waitForTimeout(NAV_DELAY);

    // If no signed URL yet for this target, try navigating away & back
    // (TikTok sometimes needs a "warm-up" navigation).
    if (!signedUrls.some(u => u.includes(encodeURIComponent(targets[i].split('/').pop())))) {
      await page.goto('https://www.tiktok.com/live', {
        waitUntil: 'domcontentloaded', timeout: 15_000,
      }).catch(() => {});
      await page.waitForTimeout(RETRY_DELAY);
      await page.goto(targets[i], {
        waitUntil: 'domcontentloaded', timeout: 15_000,
      }).catch(() => {});
      await page.waitForTimeout(NAV_DELAY);
    }
  }

  await context.unroute('**/webcast.us.tiktok.com/webcast/feed/?**');

  // Export cookies
  const cookies = await context.cookies();
  const cookieMap = {};
  for (const c of cookies) cookieMap[c.name] = c.value;

  process.stdout.write(JSON.stringify({
    cookies: cookieMap,
    signedUrls,
  }));
  process.stderr.write(`[done] ${signedUrls.length} signed URLs, ${Object.keys(cookieMap).length} cookies\n`);
} finally {
  await browser.close();
}
