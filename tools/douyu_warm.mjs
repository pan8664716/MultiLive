#!/usr/bin/env node
/**
 * 斗鱼「浏览器取参」兜底脚本（参考抖音 ttwid 预热思路）。
 *
 * 用途：纯 HTTP 取加密参数（did + websec/getEncryption）被风控时，
 *   用真实浏览器在页面内拿到同一套参数（浏览器内请求带真实上下文，
 *   站点通常放行），之后 douyu.py 仍回到纯 HTTP 拉播放地址。
 *
 * 流程：
 *   1) 打开一个斗鱼房间页，让站点种下 dy_did 等 cookie
 *   2) 在页面内调 passport did 接口拿 did（拿不到则取页面 cookie）
 *   3) 在页面内调 /wgapi/livenc/liveweb/websec/getEncryption 拿
 *      enc_data（base64 JSON，含服务端签名 sign / op{ip,ts,ua,did}）
 *   4) 输出 JSON 到 stdout：{did, enc_data, key, rand_str, enc_time,
 *      is_special, expire_at, cpp, source:"browser"}
 *
 * 用法: node douyu_warm.mjs [房间号]
 * 环境变量: HEADLESS=0 有头模式（个别风控场景更稳）
 *
 * Patchright 解析顺序：① 本机已安装的 patchright；② 抖音项目自带
 *   node_modules/patchright；③ 同级 douyin-actions 的 patchright。
 */

const RID = (process.argv[2] || '2158798').trim();
const UA =
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ' +
  '(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36';
const DID_API =
  'https://passport.douyu.com/lapi/did/api/get?client_id=1&callback=cb';
const KEY_API =
  'https://www.douyu.com/wgapi/livenc/liveweb/websec/getEncryption?did=';

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
  throw new Error(
    '未找到 patchright（npm i -D patchright 后重试，或用抖音项目的依赖）: ' +
    (last?.message || last));
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

async function main() {
  const { chromium } = await loadPatchright();
  const browser = await chromium.launch({
    executablePath: (await chromePath()) || undefined,
    headless: process.env.HEADLESS !== '0',
    args: ['--no-sandbox', '--disable-blink-features=AutomationControlled'],
  });
  try {
    const ctx = await browser.newContext({
      userAgent: UA,
      locale: 'zh-CN',
      viewport: { width: 1440, height: 900 },
    });
    const page = await ctx.newPage();
    try {
      await page.goto(`https://www.douyu.com/${RID}`,
                      { waitUntil: 'domcontentloaded', timeout: 45000 });
    } catch {
      /* 打不开房间页也继续：did 接口与 key 接口在页面内仍可直连 */
    }
    await page.waitForTimeout(4000);

    const got = await page.evaluate(async ({ didApi, keyApiBase }) => {
      const grab = async (url, referer) => {
        const r = await fetch(url, {
          headers: { 'User-Agent': navigator.userAgent,
                     Referer: referer,
                     'Accept-Language': 'zh-CN,zh;q=0.9' },
        });
        return r.text();
      };
      // 1) did：优先读页面已种下的 cookie（形如 dy_did=32位hex）
      let did = '';
      const m = document.cookie.match(/(?:^|;\s*)dy_did=([0-9a-f]{32})/i);
      if (m) did = m[1].toLowerCase();
      if (!did) {
        try {
          const txt = await grab(didApi, 'https://www.douyu.com/');
          const j = JSON.parse(txt.replace(/^cb\((.*)\)\s*$/s, '$1'));
          if (j?.data?.did) did = String(j.data.did).toLowerCase();
        } catch {}
      }
      if (!/^[0-9a-f]{32}$/.test(did)) {
        let s = '';
        for (let i = 0; i < 28; i++) s += Math.floor(Math.random() * 16).toString(16);
        did = s + '1701';
      }
      // 2) 页面内取加密参数
      const txt = await grab(keyApiBase + did, `https://www.douyu.com/`);
      const j = JSON.parse(txt);
      if (j?.error !== 0 || !j?.data) {
        throw new Error('getEncryption 异常: ' + txt.slice(0, 160));
      }
      return { did, data: j.data };
    }, { didApi: DID_API, keyApiBase: KEY_API });

    const out = {
      did: got.did,
      enc_data: got.data.enc_data,
      key: got.data.key,
      rand_str: got.data.rand_str,
      enc_time: Number(got.data.enc_time || 1),
      is_special: Number(got.data.is_special || 0),
      expire_at: Number(got.data.expire_at || 0),
      cpp: got.data.cpp || {},
      source: 'browser',
    };
    console.log(JSON.stringify(out));
  } finally {
    await browser.close();
  }
}

main().catch((e) => {
  console.error('[' + new Date().toISOString() + '] douyu_warm: ' +
                (e?.message || e));
  process.exit(1);
});
