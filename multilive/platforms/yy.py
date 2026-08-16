"""YY 直播。

抓取策略：
  列表：频道页纯 HTTP（SSR 页面内嵌全部在播房间，平台房间不多，
    一次抓全；已实测 music=28 / dancing=21 / pretty=100 左右）。
  播放地址：YY 官方播放器 SDK 走 stream-manager.yy.com/v3/channel/streams
    （带 SDK 签名/指纹，纯 HTTP 复现返回 result:2 被拒），最终
    *-flv-web.yy.com 直链只能在浏览器网络层捕获。参考 douyu_warm：
    浏览器打开房间页抓 FLV 直链（tools/yy_live.mjs），缓存到
    output/yy_warm.json，后续刷新直接复用未过期直链，只对新增/过期房间
    重新开浏览器（房间少，单轮约几分钟）。

m3u 语义：keep_stale=False，只保留在播房间。
"""
import json
import os
import re
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

from multilive.config import Source
from multilive.core import Room, Session, fmt_exc, log

NAME = 'yy'
keep_stale = False

BASE = 'https://www.yy.com/{}'
WARM_SCRIPT = 'tools/yy_live.mjs'
CACHE_NAME = 'yy_warm.json'
WARM_TTL = 30 * 60        # 直链缓存 30 分钟，超时重新开浏览器取流
WARM_TIMEOUT = 900        # 整体取流超时（房间多时放宽）
LIST_WORKERS = 3

GROUP_MAP = {
    'dancing': '舞蹈',
    'pretty': '颜值',
    'music': '音乐',
    'sing': '音乐',
}


def parse(line):
    m = re.match(r'https?://www\.yy\.com/([a-zA-Z0-9_]+)/?$', line.strip())
    if m:
        src = Source(NAME, 'category', m.group(1))
        return [src]
    return []


def fetch_category(sess, cat):
    """SSR 页面 -> {sid: {sid, ssid, title, nickname, group}}。"""
    rooms = {}
    try:
        _st, html, _ = sess.get_text(BASE.format(cat),
                                     referer='https://www.yy.com/')
    except Exception as e:
        log().info('  [频道] %s 页面失败: %s', cat, fmt_exc(e))
        return rooms
    gm = re.search(r'data-stat-name="([^"]+)"', html)
    group = GROUP_MAP.get(cat) or (gm.group(1).strip() if gm else cat)
    for block in re.split(r'<li\b', html):
        m = re.search(r'data-url="/(\d+)/(\d+)\?(?:[^"]*?tempId=\d+[^"]*?)?"'
                      r'[^>]*?data-title="([^"]*)"', block)
        if not m:
            continue
        sid, ssid, title = m.groups()
        title = (title or '').strip()
        nm = re.search(r'<span class="intro">([^<]*)</span>', block)
        nick = (nm.group(1) if nm else '').strip()
        am = re.search(r'data-original="([^"]+)"', block)
        avatar = (am.group(1) if am else '').strip()
        if not sid:
            continue
        rooms[sid] = {
            'sid': sid, 'ssid': ssid or sid,
            'title': title, 'nickname': nick or title,
            'group': group,
            'avatar': avatar,
        }
    log().info('  [频道] %s: %d 个在播房间', cat, len(rooms))
    return rooms


def cache_path(ctx):
    return os.path.join(ctx.project_root, 'output', CACHE_NAME)


def load_cache(ctx):
    try:
        with open(cache_path(ctx), encoding='utf-8') as f:
            return json.load(f) or {}
    except Exception:
        return {}


def save_cache(ctx, cache):
    try:
        os.makedirs(os.path.dirname(cache_path(ctx)), exist_ok=True)
        with open(cache_path(ctx), 'w', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False)
    except Exception as e:
        log().debug('缓存 yy_warm 失败: %s', fmt_exc(e))


def warm_rooms(ctx, rooms):
    """对新增/过期房间浏览器取流；返回 {sid: flv直链}。"""
    cache = load_cache(ctx)
    now = time.time()
    need = []
    for sid, meta in rooms.items():
        ent = cache.get(sid) or {}
        if not ent.get('url') or now - float(ent.get('ts') or 0) > WARM_TTL:
            need.append({'sid': sid, 'ssid': meta.get('ssid') or sid})
    if need:
        script = os.path.join(ctx.project_root, WARM_SCRIPT)
        if not (os.path.exists(script) and shutil.which('node')):
            log().warning('[yy] 无 %s 或 Node，本轮 %d 个房间跳过取流',
                          WARM_SCRIPT, len(need))
            return {}
        payload = json.dumps({'rooms': need, 'concurrency': 3})
        try:
            r = subprocess.run(['node', script], input=payload,
                               capture_output=True, text=True,
                               timeout=WARM_TIMEOUT)
            if r.returncode != 0:
                raise RuntimeError((r.stderr.strip() or r.stdout.strip())[-200:])
            got = json.loads(r.stdout).get('urls') or {}
            fresh = 0
            for sid, url in got.items():
                if url:
                    cache[sid] = {'url': url, 'ts': now}
                    fresh += 1
            save_cache(ctx, cache)
            log().info('  [取流] 浏览器取 %d/%d 个房间直链（缓存 %d 个）',
                       fresh, len(need), len(cache))
        except Exception as e:
            log().warning('[yy] 浏览器取流失败(%s)，沿用未过期的缓存直链',
                          fmt_exc(e))
    out = {}
    for sid in rooms:
        ent = cache.get(sid) or {}
        if ent.get('url'):
            out[sid] = ent['url']
    return out


def fetch(sources, ctx):
    log().info('[yy] %d 个来源', len(sources))
    cats = sorted({s.target for s in sources if s.kind == 'category'})
    rooms = {}
    with ThreadPoolExecutor(max_workers=LIST_WORKERS) as ex:
        results = ex.map(lambda c: fetch_category(Session(), c), cats)
        for cat_rooms in results:
            for sid, meta in cat_rooms.items():
                if sid not in rooms:
                    rooms[sid] = meta
    log().info('  [列表] 共 %d 个不重复房间', len(rooms))
    if not rooms:
        return []
    urls = warm_rooms(ctx, rooms)
    out = []
    for sid, meta in rooms.items():
        url = urls.get(sid)
        if not url:
            continue
        out.append(Room(
            platform=NAME, rid=sid,
            title=meta['title'], nickname=meta['nickname'],
            url=url, group=meta['group'] or NAME,
            avatar=meta.get('avatar') or ''))
    log().info('[yy] 完成: %d 在播房间', len(out))
    return out
