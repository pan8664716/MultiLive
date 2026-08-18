"""TikTok LIVE（浏览器批量抓取 /live 广场；纯 HTTP 拿不到列表）。

TikTok /live 为纯客户端渲染：SSR 空壳、接口需 msToken/X-Bogus 签名，
纯 HTTP 无法匿名拿列表（2026-08 实测）。用 tools/browser_fetch_tiktok.mjs
（Patchright + 系统 Chrome）打开广场页滚动加载，拦截站点自身发出的
api-live 签名请求批量收集房间（不逐房间请求）；接口拦不到时退 DOM 链接
收集 @<uniqueId>/live。
注意：依赖浏览器（Action 已装 patchright + 系统 Chrome），且 TikTok 按
地区运营（部分地区返回停服页，该平台在那些地区抓不到房间）。
m3u 语义：keep_stale=False，只保留此刻在播。
"""
import json
import os
import re
import shutil
import subprocess
import threading

from multilive.core import Room, Source, fmt_exc, log
from multilive.platforms.base import Platform

NAME = 'tiktok'
keep_stale = False

PLAYER_BASE = 'https://astar.cc.cd/tiktok/{}'
BROWSER_SCRIPT = 'browser_fetch_tiktok.mjs'
BROWSER_TIMEOUT = 240
MAX_WORKERS = 1

_browser_lock = threading.Lock()

def browser_script(ctx):
    p = os.path.join(ctx.project_root, 'tools', BROWSER_SCRIPT)
    return p if os.path.exists(p) else ''

def browser_fetch(url, ctx):
    """浏览器批量抓取（一次性页面加载，不逐房间请求）。"""
    script = browser_script(ctx)
    if not script or not shutil.which('node'):
        raise RuntimeError(
            f'缺少 Node 或 tools/{BROWSER_SCRIPT}，无法抓取 TikTok')
    with _browser_lock:   # 浏览器实例串行，避免并发启动互相干扰
        log().info('  [浏览器] %s 滚动加载中...', url)
        r = subprocess.run(['node', script, url], capture_output=True,
                           text=True, timeout=BROWSER_TIMEOUT)
    if r.returncode != 0:
        raise RuntimeError(
            '浏览器抓取失败: ' + (r.stderr.strip() or r.stdout.strip())[-300:])
    rooms = json.loads(r.stdout)
    if not isinstance(rooms, list):
        raise RuntimeError('浏览器抓取返回格式错误')
    return rooms

class TikTokPlatform(Platform):
    """TikTok 平台：浏览器批量抓取广场页；播放地址走 Worker 动态解析。"""
    name = NAME
    keep_stale = keep_stale
    max_workers = MAX_WORKERS

    def parse(self, line):
        t = line.strip()
        if re.match(r'https?://(?:www\.|m\.)?tiktok\.com/live', t):
            return [Source(self.name, 'page', t.split('?')[0])]
        if t.upper() == 'LIVE':
            return [Source(self.name, 'list', 'LIVE')]
        return []

    def fetch(self, sources, ctx):
        log().info('[tiktok] %d 个来源', len(sources))
        urls = sorted({(s.target if s.kind == 'page'
                        else 'https://www.tiktok.com/live')
                       for s in sources})
        all_rooms = {}
        for url in urls:
            try:
                rooms = browser_fetch(url, ctx)
            except Exception as e:
                log().warning('[tiktok] 浏览器抓取 %s 失败: %s', url, fmt_exc(e))
                continue
            for r in rooms:
                rid = str(r.get('rid') or '').strip()
                if rid and rid not in all_rooms:
                    all_rooms[rid] = r
        log().info('  [列表] 共 %d 个不重复房间', len(all_rooms))
        out = []
        for rid, r in all_rooms.items():
            out.append(Room(
                platform=self.name, rid=rid,
                title=(r.get('title') or '').strip(),
                nickname=(r.get('nickname') or '').strip(),
                url=PLAYER_BASE.format(rid),
                group=NAME,
                avatar=(r.get('avatar') or '').strip()))
        log().info('[tiktok] 完成: %d 在播房间（浏览器批量抓取）', len(out))
        return out

platform = TikTokPlatform()
