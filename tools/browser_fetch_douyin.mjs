#!/usr/bin/env node
/**
 * 浏览器兜底抓取（Patchright）
 *
 * 用途：纯 HTTP（接口/页面）拿不到或拿不全时，用真实浏览器抓取更多数据。
 *
 * 分类页：打开页面后**滚动加载**（触发站点自身的无限滚动，浏览器内
 *   webmssdk 会自动给接口请求补 a_bogus 签名），并**拦截接口响应**收集房间；
 *   即使出口 IP 被 HTTP 接口风控，站点自身的签约请求通常仍可成功
 *   （实测单分类可拉到约 200 个房间，远超页面静态的 15 个）。
 *
 * 直播间页：在页面主世界调 room/web/enter，webmssdk 自动补签名。
 *
 * 用法:
 *   node browser_fetch.mjs https://live.douyin.com/745350622378
 *   node browser_fetch.mjs https://live.douyin.com/categorynew/4_105
 * 环境变量:
 *   HEADLESS=0  有头模式（个别情况更稳）
 *
 * 输出: JSON 数组 [{rid, title, avatar, nickname}]
 */
import { chromium } from 'patchright';

const TARGET = process.argv[2]?.trim();
if (!TARGET) {
  console.error('用法: node browser_fetch.mjs <直播间或分类页URL>');
  process.exit(2);
}

const UA =
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ' +
  '(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36';

const CATEGORY_API = 'webcast/web/partition/detail/room/v2';
const MAX_SCROLLS = 20;      // 最多滚动次数
const SCROLL_DELAY = 1200;   // 每次滚动间隔(ms)
const IDLE_STOP = 4;         // 连续几次数量不增长就停止

let browser;
try {
  // 本机有系统 Chrome 时优先使用；CI 上没有则走下面的 Patchright 自带 Chromium
  browser = await chromium.launch({
    channel: 'chrome',
    headless: process.env.HEADLESS !== '0',
  });
} catch {
  browser = await chromium.launch({
    headless: true,
    args: ['--no-sandbox'],
  });
}

const CDN_QUALITY = ['FULL_HD1', 'HD1', 'SD1', 'SD2'];

// 从 stream_url 提取最高清 CDN 直链（hls m3u8 优先，其次 flv；转 https）
function extractCdnUrl(room) {
  const su = (room && room.stream_url) || {};
  const hls = su.hls_pull_url_map || {};
  const flv = su.flv_pull_url || {};
  for (const q of CDN_QUALITY) {
    if (hls[q]) return String(hls[q]).replace('http://', 'https://');
  }
  for (const q of CDN_QUALITY) {
    if (flv[q]) return String(flv[q]).replace('http://', 'https://');
  }
  if (su.hls_pull_url) return String(su.hls_pull_url).replace('http://', 'https://');
  return '';
}

function parseApiItem(it) {
  const roomItem = it.room || {};
  const owner = roomItem.owner || it.owner || {};
  const rid = String(it.web_rid || roomItem.web_rid || roomItem.webRid || '').trim();
  if (!/^\d{6,15}$/.test(rid)) return null;
  const title = (roomItem.title || '').trim();
  const nickname = (owner.nickname || owner.nick_name || '').trim();
  let avatar = '';
  // 该接口变体没有 owner，头像在 room.cover.url_list；逐候选取值
  for (const av of [it.avatar, owner.avatar_thumb, owner.avatar, roomItem.cover]) {
    if (av && typeof av === 'object') {
      const ul = av.url_list || [];
      if (ul.length) { avatar = String(ul[0]); break; }
    } else if (typeof av === 'string' && av) {
      avatar = av;
      break;
    }
  }
  return { rid, title, avatar, nickname, url: extractCdnUrl(roomItem) };
}

async function fetchCategory(page, path) {
  const gathered = new Map();

  // 拦截站点自己发出的分类接口响应（带 a_bogus 签名，能绕过部分 IP 风控）
  page.on('response', async (res) => {
    if (!res.url().includes(CATEGORY_API)) return;
    let j;
    try { j = await res.json(); } catch { return; }
    const items = j?.data?.data || [];
    for (const it of items) {
      const r = parseApiItem(it);
      if (r) gathered.set(r.rid, r);
    }
  });

  console.error('[browser] 打开分类页并滚动加载 ...');
  await page.goto(TARGET, { waitUntil: 'domcontentloaded', timeout: 60_000 });
  await page.waitForTimeout(8000);

  // 缓存可滚动容器，之后每轮直接滚到底
  await page.evaluate(() => {
    window.__scrollers = [...document.querySelectorAll('div')]
      .filter((el) => el.scrollHeight > el.clientHeight + 300);
  });

  let lastCount = 0;
  let idle = 0;
  for (let i = 0; i < MAX_SCROLLS; i++) {
    await page.evaluate(() => {
      window.scrollTo(0, document.documentElement.scrollHeight);
      document.documentElement.scrollTop = document.documentElement.scrollHeight;
      for (const el of window.__scrollers || []) el.scrollTop = el.scrollHeight;
    });
    await new Promise((r) => setTimeout(r, SCROLL_DELAY));
    const cards = await page.evaluate(() =>
      [...document.querySelectorAll('a[href*="live.douyin.com/"]')]
        .filter((a) => /live\.douyin\.com\/\d{6,15}/.test(a.href)).length);
    if (cards === lastCount) {
      if (++idle >= IDLE_STOP) break;
    } else {
      idle = 0;
      lastCount = cards;
    }
  }
  // 等拦截到的响应都处理完
  await page.waitForTimeout(1500);

  let rooms = [...gathered.values()];
  if (!rooms.length) {
    // 接口一个都没拦到：退而求其次，从 DOM 链接收集房间号（标题留空）
    rooms = await page.evaluate(() =>
      [...new Set([...document.querySelectorAll('a[href*="live.douyin.com/"]')]
        .map((a) => (a.href.match(/live\.douyin\.com\/(\d{6,15})/) || [])[1])
        .filter(Boolean))]
        .map((rid) => ({ rid, title: '', avatar: '', nickname: '', url: '' })));
  }
  return rooms;
}

async function fetchRoom(page, rid) {
  console.error('[browser] 打开直播间并请求 enter 接口 ...');
  await page.goto(TARGET, { waitUntil: 'domcontentloaded', timeout: 60_000 });
  await page.waitForTimeout(8000);

  const rooms = await page.evaluate(async (roomId) => {
    const html = document.documentElement.innerHTML;
    const m1 = html.match(/"roomId":"(\d+)"/);
    const m2 = html.match(/roomId&quot;:&quot;(\d+)&quot;/);
    const roomIdStr = (m1 && m1[1]) || (m2 && m2[1]) || '';

    const nav = navigator;
    const params = {
      aid: '6383', app_name: 'douyin_web', live_id: '1',
      device_platform: 'web', language: 'zh-CN', enter_from: 'link_share',
      cookie_enabled: String(nav.cookieEnabled),
      screen_width: String(screen.width), screen_height: String(screen.height),
      browser_language: nav.language, browser_platform: nav.platform,
      browser_name: 'Chrome',
      browser_version: (nav.userAgent.match(/Chrome\/(\S+)/) || [])[1] || '151.0.0.0',
      os_name: 'Windows', os_version: '10',
      web_rid: roomId, room_id_str: roomIdStr,
      enter_source: '', is_need_double_stream: 'false',
      insert_task_id: '', live_reason: '',
    };
    const qs = Object.entries(params)
      .map(([k, v]) => `${k}=${encodeURIComponent(v ?? '')}`)
      .join('&');
    const res = await fetch(`https://live.douyin.com/webcast/room/web/enter/?${qs}`, {
      headers: { accept: 'application/json' },
    });
    const txt = await res.text();
    let j;
    try { j = JSON.parse(txt); }
    catch { throw new Error('enter接口返回非JSON(status=' + res.status + '): ' + txt.slice(0, 120)); }
    const d = j?.data?.data?.[0];
    if (!d) return [];
    const user = d.owner || d.user || {};
    let avatar = '';
    const av = user.avatar_thumb || user.avatar || {};
    if (av && typeof av === 'object') {
      const ul = av.url_list || [];
      if (ul.length) avatar = String(ul[0]);
    } else if (typeof av === 'string') {
      avatar = av;
    }
    return [{
      rid: roomId,
      title: d.title || '',
      avatar,
      nickname: user.nickname || user.nick_name || '',
      url: extractCdnUrl(d),
    }];
  }, rid);
  return rooms;
}

try {
  const context = await browser.newContext({ userAgent: UA, locale: 'zh-CN' });
  const page = await context.newPage();

  const category = TARGET.match(/categorynew\/([\d_]+)/);
  const room = TARGET.match(/live\.douyin\.com\/(\d+)/);
  if (!category && !room) {
    console.error('无法识别的 URL: ' + TARGET);
    process.exit(2);
  }

  const rooms = category ? await fetchCategory(page, category[1]) : await fetchRoom(page, room[1]);
  console.log(JSON.stringify(rooms));
  if (!rooms || rooms.length === 0) {
    console.error('[browser] 未取到任何房间数据');
    process.exit(1);
  }
} finally {
  await browser.close();
}
