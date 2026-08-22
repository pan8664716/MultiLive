"""TikTok LIVE 分类页批量抓取（浏览器签名 + 纯 HTTP 获取数据）。

流程：
1. tools/browser_fetch_tiktok.mjs 打开一个浏览器会话，依次访问所有
   分类页让 webmssdk SDK 签名 feed 请求；在 route 层 abort 并捕获完整
   签名 URL，同时导出 ttwid/msToken 等 cookies。
2. Python 侧用 multilive.core.Session 携带 cookies 重放每个签名 URL，
   解析 data[].data.owner.display_id 等字段去重后批量输出房间。
3. 浏览器只负责签名和 cookie 建立，所有实际数据请求走纯 HTTP。

播放地址统一写 https://astar.cc.cd/tiktok/<uniqueId>。
m3u 语义：keep_stale=False，只保留此刻在播。
"""
import json
import os
import re
import shutil
import subprocess

from multilive.core import Room, Session, Source, fmt_exc, log, UA_CHROME
from multilive.platforms.base import Platform

NAME = 'tiktok'
keep_stale = False

PLAYER_BASE = 'https://astar.cc.cd/tiktok/{}'
BROWSER_SCRIPT = 'browser_fetch_tiktok.mjs'
BROWSER_TIMEOUT = 180
MAX_WORKERS = 1


def _pick(v):
    if isinstance(v, str) and v.strip():
        return v.strip()
    return ''


def _avatar(obj):
    if not obj or not isinstance(obj, dict):
        return ''
    for key in ('url_list', 'urlList', 'image_urls'):
        lst = obj.get(key)
        if isinstance(lst, list) and lst:
            return str(lst[0])
    return ''


class TikTokPlatform(Platform):
    """TikTok 平台：浏览器签名一次 → 纯 HTTP 批量获取所有分类。"""
    name = NAME
    keep_stale = keep_stale
    max_workers = MAX_WORKERS

    def parse(self, line):
        t = line.strip()
        if re.match(r'https?://(?:www\.|m\.)?tiktok\.com/live', t):
            return [Source(self.name, 'page', t.split('?')[0])]
        if t.upper() == 'LIVE':
            return [Source(self.name, 'page', 'https://www.tiktok.com/live')]
        return []

    def fetch(self, sources, ctx):
        log().info('[tiktok] %d 个来源', len(sources))
        urls = sorted({(s.target if s.kind == 'page'
                        else 'https://www.tiktok.com/live')
                       for s in sources})

        # Step 1: browser signing
        signed_data = self._browser_sign(urls, ctx)
        if not signed_data or not signed_data.get('signedUrls'):
            log().warning('[tiktok] 未获取到签名 URL')
            return []

        cookies = signed_data.get('cookies', {})
        signed_urls = signed_data['signedUrls']
        log().info('[tiktok] %d 个签名 URL, %d 个 cookies',
                   len(signed_urls), len(cookies))

        # Step 2: pure HTTP replay with cookies
        cookie_str = '; '.join(f'{k}={v}' for k, v in cookies.items()
                               if k in ('ttwid', 'tt_csrf_token',
                                        'tt_chain_token', 'msToken'))
        session = Session(headers={
            'User-Agent': UA_CHROME,
            'Referer': 'https://www.tiktok.com/',
            'Cookie': cookie_str,
        })

        all_rooms = {}
        for i, url in enumerate(signed_urls):
            try:
                st, data, _ = session.get_json(url, timeout=20)
                rooms_raw = data.get('data') if isinstance(data, dict) else None
                if not isinstance(rooms_raw, list):
                    continue
                for item in rooms_raw:
                    inner = item.get('data') or item
                    owner = inner.get('owner') or {}
                    rid = (_pick(owner.get('display_id')) or
                           _pick(owner.get('unique_id')))
                    if not rid or rid in all_rooms:
                        continue
                    all_rooms[rid] = {
                        'rid': rid,
                        'title': _pick(inner.get('title')),
                        'nickname': _pick(owner.get('nickname')),
                        'avatar': (_avatar(owner.get('avatar_thumb')) or
                                   _avatar(owner.get('avatarThumb')) or
                                   _avatar(owner.get('avatarLarger')) or
                                   _avatar(owner.get('avatar_medium')) or
                                   _avatar(owner.get('avatar'))),
                    }
                log().info('[tiktok] HTTP[%d/%d] status=%d ok',
                           i + 1, len(signed_urls), st)
            except Exception as e:
                log().warning('[tiktok] HTTP[%d/%d] 失败: %s',
                              i + 1, len(signed_urls), fmt_exc(e))

        log().info('[tiktok] 共 %d 个不重复房间', len(all_rooms))

        out = []
        for rid, r in all_rooms.items():
            out.append(Room(
                platform=self.name,
                rid=rid,
                title=r.get('title', ''),
                nickname=r.get('nickname', ''),
                url=PLAYER_BASE.format(rid),
                group=NAME,
                avatar=r.get('avatar', ''),
            ))
        log().info('[tiktok] 完成: %d 在播房间', len(out))
        return out

    def _browser_sign(self, urls, ctx):
        script_path = os.path.join(ctx.project_root, 'tools', BROWSER_SCRIPT)
        if not os.path.exists(script_path) or not shutil.which('node'):
            log().warning('[tiktok] 缺少 Node 或 %s', BROWSER_SCRIPT)
            return None

        cmd = ['node', script_path] + list(urls)
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               timeout=BROWSER_TIMEOUT)
        except subprocess.TimeoutExpired:
            log().warning('[tiktok] 浏览器签名超时(%ds)', BROWSER_TIMEOUT)
            return None

        if r.returncode != 0:
            log().warning('[tiktok] 浏览器签名失败: %s',
                          (r.stderr.strip() or r.stdout.strip())[-200:])
            return None

        try:
            data = json.loads(r.stdout.strip())
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, ValueError) as e:
            log().warning('[tiktok] 解析浏览器输出失败: %s', fmt_exc(e))
        return None


platform = TikTokPlatform()
