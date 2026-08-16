"""快手直播（源自 kuaishou 方案，纯 HTTP，无浏览器）。

数据源 live_api/hot/list 与页面下拉加载一致：免登录免签名，
每页 50 个在播房间 + 4 档清晰度 CDN 直链（FLV，签名 24h 有效）。
m3u 语义：keep_stale=False——只保留「此刻在播」列表，下播即失效。
"""
import re
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed

from multilive.config import Source
from multilive.core import Room, Session, fmt_exc, log

NAME = 'kuaishou'
keep_stale = False

HOT_API = 'https://live.kuaishou.com/live_api/hot/list'
DEFAULT_PAGES = 50
MAX_PAGES = 50
PAGE_WORKERS = 5
MAX_SOURCE_WORKERS = 5
REQUEST_TIMEOUT = 20


def parse(line):
    t = line.strip()
    if not t:
        return []
    name = t
    pages = DEFAULT_PAGES
    if ':' in t.split('/')[-1]:
        name, _, pages_s = t.rpartition(':')
        try:
            pages = int(pages_s)
        except ValueError:
            pass
    name = (name.split('/live/')[-1].rstrip('/') or '').upper()
    if not re.fullmatch(r'[A-Z0-9]+', name or ''):
        return []
    src = Source(NAME, 'list', name)
    src.meta = pages
    return [src]


def fetch_page(sess, source, page):
    url = f'{HOT_API}?{urllib.parse.urlencode({
        "type": source, "filterType": 0, "page": page, "pageSize": 24})}'
    try:
        _st, j, _ = sess.get_json(
            url, timeout=REQUEST_TIMEOUT,
            referer=f'https://live.kuaishou.com/live/{source}')
        return page, (j.get('data') or {}).get('list') or []
    except Exception as e:
        log().info('  [%s] 第%d页失败: %s', source, page, fmt_exc(e))
        return page, []


def best_play_url(room):
    reps = []
    for pu in room.get('playUrls') or []:
        reps.extend((pu.get('adaptationSet') or {}).get('representation') or [])
    if not reps:
        return None
    reps = sorted(reps, key=lambda x: (x.get('level', 0), x.get('bitrate', 0)))
    return reps[-1].get('url')


def fetch_source(sess, source, pages):
    rooms = {}
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=min(PAGE_WORKERS, pages)) as pool:
        futs = {pool.submit(fetch_page, sess, source, p): p
                for p in range(1, pages + 1)}
        for fut in as_completed(futs):
            _page, items = fut.result()
            for room in items:
                lid = room.get('id')
                if lid and lid not in rooms:
                    rooms[lid] = room
    log().info('  [%s] %d页抓完: %d 个不重复房间, 用时 %.1fs',
               source, pages, len(rooms), time.time() - t0)
    return rooms


def fetch(sources, ctx):
    log().info('[kuaishou] %d 个来源', len(sources))
    all_rooms = {}
    with ThreadPoolExecutor(max_workers=MAX_SOURCE_WORKERS) as pool:
        futs = {}
        for s in sources:
            sess = Session()
            pages = min(s.meta if isinstance(s.meta, int) else DEFAULT_PAGES,
                        MAX_PAGES)
            if ctx.pages_cap:
                pages = min(pages, ctx.pages_cap)
            futs[pool.submit(fetch_source, sess, s.target, pages)] = s
        for fut, s in futs.items():
            try:
                for lid, room in fut.result().items():
                    if lid not in all_rooms:
                        all_rooms[lid] = room
            except Exception as e:
                log().warning('[kuaishou] 来源 %s 失败: %s', s.target, fmt_exc(e))

    out = []
    for lid, room in all_rooms.items():
        author = room.get('author') or {}
        game = ((room.get('gameInfo') or {}).get('name') or '').strip() or NAME
        url = best_play_url(room)
        if not url:
            continue
        out.append(Room(platform=NAME, rid=str(lid),
                        title=(room.get('caption') or '').strip(),
                        nickname=(author.get('name') or '').strip(),
                        url=url.replace('http://', 'https://'),
                        group=game,
                        avatar=(room.get('cover') or '')))
    log().info('[kuaishou] 完成: %d 房间（无地址已跳过）', len(out))
    return out
