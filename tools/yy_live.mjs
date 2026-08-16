#!/usr/bin/env node
/**
 * YY 直播「浏览器取流」脚本。
 *
 * 背景：YY 播放地址必须走官方播放器 SDK 的
 *   stream-manager.yy.com/v3/channel/streams 接口（带 SDK 签名/指纹，
 *   纯 HTTP 复现返回 result:2），最终 FLV 直链只能在浏览器里捕获，
 *   因此参考 douyu_warm 的思路：浏览器打开房间页 -> 网络层抓
 *   *-flv-web.yy.com 的 FLV 请求 -> 输出直链，之后 yy.py 仅做纯 HTTP 列表。
 *
 * 用法:
 *   echo '{"rooms":[{"sid":"54880976","ssid":"54880976"}],"concurrency":3}' \
 *     | node yy_live.mjs
 * 输出(stdout JSON): {"urls":{"<sid>":"<flv直链>"}}
 * 环境变量: HEADLESS=0 有头模式（个别风控场景更稳）
 */

import { readFileSync } from 'node:fs';

const UA =
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ' +
  '(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36';
const FLV_RE = /https:\/\/[\w.-]+-flv-web\.yy\.com\/live\/[^\s"']+\.flv(?:\?[^\s"']*)?/;
const PER_ROOM_TIMEOUT = 25000;

async function loadPatchright() {
  const candidates = [
    'patchright',
    '/Users/star/Downloads/douyin-actions/node_modules/patchright/index.mjs',
    new URL('../../douyin-actions/node_modules/patchright/index.mjs',
            import.meta.url).pathname,
  ];
  let last;
  for (const p of candidates) {
    try {
      return await import(p);
    } catch (e) {
      last = e;
    }
  }
  throw new Error('未找到 patchright: ' + (last?.message || last));
}

async function chromePath() {
  const { existsSync } = await import('node:fs');
  return [
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
    '/usr/bin/google-chrome',
    '/usr/bin/chromium-browser',
  ].find((p) => existsSync(p)) || '';
}

function parseInput() {
  let raw = '';
  try { raw = readFileSync(0, 'utf8'); } catch {}
  let j = {};
  try { j = JSON.parse(raw || '{}'); } catch {}
  const rooms = (j.rooms || []).filter(
    (r) => r && /^\d+$/.test(String(r.sid || '')));
  return { rooms, concurrency: Math.max(1, Math.min(6, j.concurrency || 3)) };
}

function captureOne(page, sid, ssid) {
  return new Promise((resolve) => {
    let done = false;
    const finish = (u) => { if (!done) { done = true; clearTimeout(timer); resolve(u); } };
    const timer = setTimeout(() => finish(null), PER_ROOM_TIMEOUT);
    const onResp = (res) => {
      const m = res.url().match(FLV_RE);
      if (m) finish(m[0]);
    };
    page.on('response', onResp);
    page.goto(`https://www.yy.com/${sid}/${ssid || sid}`,
              { waitUntil: 'domcontentloaded', timeout: 15000 })
      .catch(() => {});
  });
}

async function main() {
  const { rooms, concurrency } = parseInput();
  if (!rooms.length) {
    console.log(JSON.stringify({ urls: {} }));
    return;
  }
  const { chromium } = await loadPatchright();
  const browser = await chromium.launch({
    executablePath: (await chromePath()) || undefined,
    headless: process.env.HEADLESS !== '0',
    args: ['--no-sandbox', '--disable-blink-features=AutomationControlled'],
  });
  const urls = {};
  let idx = 0;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  async function worker() {
    const ctx = await browser.newContext({
      userAgent: UA, locale: 'zh-CN',
      viewport: { width: 1280, height: 800 },
    });
    const page = await ctx.newPage();
    try {
      while (idx < rooms.length) {
        const r = rooms[idx++];
        await sleep(300 + Math.random() * 500); // 错峰，避免短时间并发拉满
        try {
          const url = await captureOne(page, r.sid, r.ssid);
          if (url) urls[String(r.sid)] = url;
        } catch {
          /* 单房间失败不影响整体 */
        } finally {
          page.removeAllListeners('response');
        }
      }
    } finally {
      await ctx.close();
    }
  }

  await Promise.all(Array.from({ length: concurrency }, () => worker()));
  await browser.close();
  console.log(JSON.stringify({ urls }));
}

main().catch((e) => {
  console.error('[' + new Date().toISOString() + '] yy_live: ' +
                (e?.message || e));
  process.exit(1);
});
