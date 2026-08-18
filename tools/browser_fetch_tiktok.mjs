#!/usr/bin/env node
/**
 * TikTok LIVE 列表浏览器兜底抓取（Patchright）
 *
 * 用途：TikTok /live 页面为纯客户端渲染、接口需 msToken/X-Bogus 签名，
 *   纯 HTTP 拿不到（SSR 空壳）。用真实浏览器打开直播间广场页并滚动加载，
 *   拦截站点自身发出的 api-live 签名请求收集房间（批量，不逐房间请求）；
 *   接口一个都拦不到时退回 DOM 链接收集 @<uniqueId>/live。
 *
 * 用法:
 *   node browser_fetch_tiktok.mjs https://www.tiktok.com/live
 * 环境变量:
 *   HEADLESS=0  有头模式（个别情况更稳）
 *
 * 输出: JSON 数组 [{rid, title, avatar, nickname, url}]
 *   rid = 用户 uniqueId（跨场次稳定，与快手 author.id 同思路）
 */
import { chromium } from 'patchright';

const TARGET = process.argv[2]?.trim() || 'https://www.tiktok.com/live';

const UA =
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ' +
  '(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36';

const MAX_SCROLLS = 15;
const SCROLL_DELAY = 1500;
const IDLE_STOP = 4;

let browser;
try {
  browser = await chromium.launch({
    channel: 'chrome',
    headless: process.env.HEADLESS !== '0',
  });
} catch {
  browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
}

function pickStr(v) {
  if (typeof v === 'string' && v) return v.trim();
  return '';
}

function parseUserItem(it) {
  const user = it.user || it.liveRoomUser || {};
  const room = it.live_room || it.liveRoom || it.room || {};
  const rid = pickStr(user.unique_id || user.uniqueId);
  if (!rid) return null;
  let avatar = '';
  for (const av of [user.avatar_thumb, user.avatarThumb, user.avatarLarger, user.avatar, room.cover]) {
    if (av && typeof av === 'object') {
      const ul = av.url_list || av.urlList || [];
      if (ul.length) { avatar = String(ul[0]); break; }
    } else if (typeof av === 'string' && av) {
      avatar = av;
      break;
    }
  }
  return {
    rid,
    title: pickStr(room.title || room.liveTitle || it.title),
    nickname: pickStr(user.nickname),
    avatar,
    url: '',
  };
}

async function fetchLive(page) {
  const gathered = new Map();

  // ① 优先读 SSR 内嵌数据（部分区域 /live 会 SSR 出 __UNIVERSAL_DATA_FOR_REHYDRATION__）
  const fromSsr = await page.evaluate(() => {
    const findLiveRooms = (obj, out) => {
      if (!obj || typeof obj !== 'object') return;
      if (Array.isArray(obj)) {
        for (const it of obj) findLiveRooms(it, out);
        return;
      }
      for (const key of Object.keys(obj)) {
        const v = obj[key];
        if (/liveRoomUserInfoList/i.test(key) && Array.isArray(v)) {
          for (const it of v) out.push(it);
        } else if (v && typeof v === 'object') {
          findLiveRooms(v, out);
        }
      }
    };
    const out = [];
    for (const id of ['__UNIVERSAL_DATA_FOR_REHYDRATION__', 'SIGI_STATE']) {
      const el = document.getElementById(id);
      if (!el) continue;
      try {
        findLiveRooms(JSON.parse(el.textContent), out);
      } catch { /* 忽略解析失败，继续其它来源 */ }
    }
    return out;
  });
  for (const it of fromSsr) {
    const r = parseUserItem(it);
    if (r && r.rid && !gathered.has(r.rid)) gathered.set(r.rid, r);
  }
  if (gathered.size) {
    console.error('[browser] SSR 内嵌数据: %d 个房间', gathered.size);
  }

  // ② 拦截站点自己的 api-live 签名请求（能绕过 IP 风控 / 签名校验）
  page.on('response', async (res) => {
    if (!res.url().includes('api-live')) return;
    let j;
    try { j = await res.json(); } catch { return; }
    const items = Array.isArray(j?.data) ? j.data
      : Array.isArray(j?.user_list) ? j.user_list : null;
    if (!items) return;
    for (const it of items) {
      const r = parseUserItem(it);
      if (r && r.rid && !gathered.has(r.rid)) gathered.set(r.rid, r);
    }
  });

  console.error('[browser] 打开 TikTok LIVE 广场并滚动加载 ...');
  await page.goto(TARGET, { waitUntil: 'domcontentloaded', timeout: 90_000 });
  await page.waitForTimeout(10000);

  const cardHref = () => page.evaluate(() =>
    [...document.querySelectorAll('a[href*="/live"]')]
      .map((a) => a.href || '')
      .filter((h) => /tiktok\.com\/@[^/]+\/live/.test(h) || /\/@[^/]+\/live/.test(h)));

  let lastCount = 0;
  let idle = 0;
  for (let i = 0; i < MAX_SCROLLS; i++) {
    await page.evaluate(() => {
      window.scrollTo(0, document.documentElement.scrollHeight);
      document.documentElement.scrollTop = document.documentElement.scrollHeight;
      for (const el of document.querySelectorAll('div')) {
        if (el.scrollHeight > el.clientHeight + 300) el.scrollTop = el.scrollHeight;
      }
    });
    await new Promise((r) => setTimeout(r, SCROLL_DELAY));
    const n = (await cardHref()).length;
    if (n === lastCount) {
      if (++idle >= IDLE_STOP) break;
    } else {
      idle = 0;
      lastCount = n;
    }
  }
  await page.waitForTimeout(1500);

  let rooms = [...gathered.values()];
  if (!rooms.length) {
    // 接口没拦到：从 DOM 链接收集 @<uniqueId>/live（标题/昵称尽量从卡片补）
    const cards = await page.evaluate(() => {
      const out = new Map();
      for (const a of document.querySelectorAll('a[href*="/live"]')) {
        const m = (a.href || '').match(/(?:tiktok\.com)?\/@([^/]+)\/live/);
        if (!m) continue;
        const rid = m[1];
        if (!rid || out.has(rid)) continue;
        const img = a.querySelector('img');
        const title = a.getAttribute('title') || (a.querySelector('[data-e2e="live-title"], .css-*')?.textContent || '');
        out.set(rid, {
          rid,
          title: (title || '').trim(),
          nickname: '',
          avatar: img?.src || img?.getAttribute('src') || '',
          url: '',
        });
      }
      return [...out.values()];
    });
    rooms = cards;
  }
  return rooms;
}

try {
  const context = await browser.newContext({ userAgent: UA, locale: 'en-US' });
  const page = await context.newPage();
  const rooms = await fetchLive(page);
  console.log(JSON.stringify(rooms));
  if (!rooms || rooms.length === 0) {
    console.error('[browser] 未取到任何房间数据');
    process.exit(1);
  }
} finally {
  await browser.close();
}
