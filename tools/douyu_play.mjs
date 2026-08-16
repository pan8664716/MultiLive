#!/usr/bin/env node
/**
 * 斗鱼播放地址解析（Node 兜底，纯 Python 无法复刻站点签名 JS）。
 *
 * 流程：
 *   1) GET swf_api/homeH5Enc?rids=<房间号> 取动态签名函数（42KB 混淆 JS）
 *   2) eval 该函数（提供 CryptoJS.MD5 shim），计算 sign/v/did/tt
 *   3) GET lapi/live/getH5Play/<房间号> 拿播放地址
 *
 * 用法: node douyu_play.mjs <房间号>
 * 输出: JSON {"rid":..., "url":...}；失败退出码非 0 并把原因打到 stderr。
 *
 * 状态（2026-08 实测）：签名 JS 可执行并产出 sign，但 getH5Play 返回
 * 403 "鉴权失败"，应还缺播放器请求的会话标识；站点策略变化后在此跟进。
 */
import { createHash } from 'crypto';

// 签名 JS 需要 CryptoJS.MD5。只用到 toString() 的 hex 形式，这里给最小实现。
globalThis.CryptoJS = {
  MD5: (s) => ({ toString: () => createHash('md5').update(String(s)).digest('hex') }),
};

const UA =
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ' +
  '(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36';

async function main() {
  const rid = process.argv[2]?.trim();
  if (!/^\d+$/.test(rid || '')) {
    console.error('用法: node douyu_play.mjs <房间号>');
    process.exit(2);
  }
  const referer = `https://www.douyu.com/${rid}`;

  const enc = await fetch(`https://www.douyu.com/swf_api/homeH5Enc?rids=${rid}`, {
    headers: { 'User-Agent': UA, Referer: referer },
  });
  const j = await enc.json();
  const code = j?.data?.[`room${rid}`];
  if (!code) {
    console.error('homeH5Enc 未返回签名函数');
    process.exit(3);
  }
  const fnName = (code.match(/function\s+([A-Za-z0-9_]+)\s*\(/) || [])[1];
  if (!fnName) {
    console.error('无法识别签名函数名');
    process.exit(3);
  }
  eval(code + `;globalThis.__douyuSign=${fnName};`);

  const did = [...crypto.getRandomValues(new Uint8Array(16))]
    .map((b) => b.toString(16).padStart(2, '0')).join('');
  const tt = String(Math.floor(Date.now() / 1000));
  const q = new URLSearchParams(globalThis.__douyuSign(rid, did, tt));
  q.set('rid', rid);

  const r = await fetch(`https://www.douyu.com/lapi/live/getH5Play/${rid}?${q}`, {
    headers: { 'User-Agent': UA, Referer: referer },
  });
  const txt = await r.text();
  let body;
  try { body = JSON.parse(txt); } catch { body = null; }
  if (r.status !== 200 || !body) {
    console.error(`getH5Play ${r.status}: ${txt.slice(0, 120)}`);
    process.exit(4);
  }
  const data = body.data || {};
  const url = data.hls_url || data.rtmp_url || (data.rtmp_url || '') + (data.rtmp_live || '');
  if (!url) {
    console.error('getH5Play 未返回播放地址: ' + txt.slice(0, 160));
    process.exit(4);
  }
  console.log(JSON.stringify({ rid, url, rate: data.rate, multi: !!data.multiaudio }));
}

main().catch((e) => {
  console.error('解析失败: ' + (e?.message || e));
  process.exit(1);
});
