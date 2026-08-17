"""虎牙直播（列表纯 HTTP；播放地址统一走代理，不逐房间取流）。

列表：cache.php?m=LiveList&do=getLiveListByPage —— 整站在播房间列表
  （120 房间/页），条目里 profileRoom 是真正的房间号，nick/roomName/
  gameFullName/screenshot 直接可用。

播放地址：不逐房间解析（页面内嵌 FLV/HLS 直链 2026-08 实测已失效，
  P2P slice 为私有格式，标准播放器无法直接解码，详见 PLATFORMS.md）。
  m3u 条目统一写 https://astar.cc.cd/huya/<房间号> ——
  Worker 点播时实时解析出完整签名直链，看哪个解析哪个。

m3u 语义：keep_stale=False，只保留此刻在播。
"""
import re
from concurrent.futures import ThreadPoolExecutor

from multilive.core import Room, Session, Source, fmt_exc, log
from multilive.platforms.base import Platform

NAME = 'huya'
keep_stale = False

LIVE_LIST_API = 'https://www.huya.com/cache.php'
PLAYER_BASE = 'https://astar.cc.cd/huya/{}'
DEFAULT_PAGES = 10        # 默认扫 10 页列表（1200 房间）
MAX_PAGES = 79
LIST_WORKERS = 5


def fetch_list_page(sess, page):
    url = (f'{LIVE_LIST_API}?m=LiveList&do=getLiveListByPage'
           f'&tagAll=0&page={page}')
    try:
        _st, j, _ = sess.get_json(url, referer='https://www.huya.com/l')
        return (j.get('data') or {}).get('datas') or []
    except Exception as e:
        log().info('  [列表] 第%d页失败: %s', page, fmt_exc(e))
        return []


def fetch_list(sources, ctx):
    """并发翻列表，去重房间号，返回 {rid: 房间元数据}。"""
    pages = max(s.meta or DEFAULT_PAGES for s in sources)
    if ctx.pages_cap:
        pages = min(pages, ctx.pages_cap)
    rooms = {}
    with ThreadPoolExecutor(max_workers=LIST_WORKERS) as ex:
        futs = {ex.submit(fetch_list_page, Session(), p): p
                for p in range(1, pages + 1)}
        for fut in futs:
            for it in fut.result():
                rid = str(it.get('profileRoom') or '').strip()
                if rid and rid not in rooms:
                    rooms[rid] = {
                        'nickname': (it.get('nick') or '').strip(),
                        'title': (it.get('roomName') or '').strip(),
                        'group': (it.get('gameFullName') or '').strip() or NAME,
                        'avatar': (it.get('screenshot') or '').strip(),
                    }
    log().info('  [列表] %d页共 %d 个不重复房间', pages, len(rooms))
    return rooms


class HuyaPlatform(Platform):
    """虎牙平台：列表纯 HTTP，播放地址走代理动态解析。"""
    name = NAME
    keep_stale = keep_stale
    max_workers = LIST_WORKERS

    def parse(self, line):
        t = line.strip()
        m = re.match(r'https?://www\.huya\.com/l(:\d+)?', t)
        if not m:
            return []
        pages = DEFAULT_PAGES
        if m.group(1):
            try:
                pages = int(m.group(1)[1:])
            except ValueError:
                pass
        src = Source(self.name, 'list', 'ALL')
        src.meta = min(max(pages, 1), MAX_PAGES)
        return [src]

    def fetch(self, sources, ctx):
        log().info('[huya] %d 个来源', len(sources))
        rooms = fetch_list(sources, ctx)
        out = []
        for rid, meta in rooms.items():
            out.append(Room(
                platform=self.name, rid=rid,
                title=meta['title'], nickname=meta['nickname'],
                url=PLAYER_BASE.format(rid),
                group=meta['group'] or self.name, avatar=meta['avatar']))
        log().info('[huya] 完成: %d 在播房间（地址走代理动态解析，未逐个取流）',
                   len(out))
        return out


platform = HuyaPlatform()
