"""YY 直播。

抓取策略：
  列表：频道页纯 HTTP（SSR 页面内嵌全部在播房间，平台房间不多，
    一次抓全；已实测 music=28 / dancing=21 / pretty=100 左右）。
  播放地址：不逐个取流（避免风控）。m3u 里的地址直接写成
    https://douyin-m3u8.pages.dev/yy/<房间号> —— Cloudflare Worker
    侧在点播时实时解析出最高画质 FLV 直链并 302，看哪个解析哪个。

m3u 语义：keep_stale=False，只保留在播房间。
"""
import re
from concurrent.futures import ThreadPoolExecutor

from multilive.config import Source
from multilive.core import Room, Session, fmt_exc, log

NAME = 'yy'
keep_stale = False

BASE = 'https://www.yy.com/{}'
PLAYER_BASE = 'https://douyin-m3u8.pages.dev/yy/{}'
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
    out = []
    for sid, meta in rooms.items():
        out.append(Room(
            platform=NAME, rid=sid,
            title=meta['title'], nickname=meta['nickname'],
            url=PLAYER_BASE.format(sid), group=meta['group'] or NAME,
            avatar=meta.get('avatar') or ''))
    log().info('[yy] 完成: %d 在播房间（地址走 pages.dev 动态解析，未逐个取流）',
               len(out))
    return out
