"""斗鱼直播。

抓取策略（2026-08 实测）：
  目录：GET gapi/rkc/directory/0_0/<page>（directory/all 同款接口，
    120 房间/页，含昵称/房间名/分类/封面）。

  播放地址：不逐房间调 getH5PlayV1 取流（直链有签名时效：签发后几分钟
    CDN 就只吐 1-2s 缓冲窗口并断开，无法支撑 m3u 长期播放）。m3u 条目
    直接写 https://astar.cc.cd/douyu/<房间号> —— Worker 点播时实时解析
    新签名直链，看哪个解析哪个。

m3u 语义：keep_stale=False，只保留此刻在播。
"""
import re
from concurrent.futures import ThreadPoolExecutor

from multilive.core import Room, Session, Source, fmt_exc, log
from multilive.platforms.base import Platform

NAME = 'douyu'
keep_stale = False

DIR_API = 'https://www.douyu.com/gapi/rkc/directory/0_0/{}'
PLAYER_BASE = 'https://astar.cc.cd/douyu/{}'
DEFAULT_PAGES = 10
MAX_PAGES = 65
LIST_WORKERS = 5


def fetch_dir_page(sess, page):
    url = DIR_API.format(page)
    try:
        _st, j, _ = sess.get_json(
            url, referer='https://www.douyu.com/directory/all')
        return (j.get('data') or {}).get('rl') or []
    except Exception as e:
        log().info('  [目录] 第%d页失败: %s', page, fmt_exc(e))
        return []


def fetch_dir(sources, ctx):
    pages = max(s.meta or DEFAULT_PAGES for s in sources)
    if ctx.pages_cap:
        pages = min(pages, ctx.pages_cap)
    rooms = {}
    with ThreadPoolExecutor(max_workers=LIST_WORKERS) as ex:
        futs = {ex.submit(fetch_dir_page, Session(), p): p
                for p in range(1, pages + 1)}
        for fut in futs:
            for it in fut.result():
                rid = str(it.get('rid') or '').strip()
                if rid and rid not in rooms:
                    rooms[rid] = {
                        'rid': rid,
                        'nickname': (it.get('nn') or '').strip(),
                        'title': (it.get('rn') or '').strip(),
                        'group': (it.get('c2name') or '').strip() or NAME,
                        'avatar': (it.get('rs16') or it.get('rs1') or ''),
                    }
    log().info('  [目录] %d页共 %d 个不重复房间', pages, len(rooms))
    return rooms


class DouyuPlatform(Platform):
    """斗鱼平台：目录列表纯 HTTP；播放地址统一走 Worker 动态解析。"""
    name = NAME
    keep_stale = keep_stale
    max_workers = LIST_WORKERS

    def parse(self, line):
        t = line.strip()
        m = re.match(r'https?://www\.douyu\.com/directory/all(:\d+)?', t)
        if not m:
            return []
        pages = DEFAULT_PAGES
        if m.group(1):
            try:
                pages = int(m.group(1)[1:])
            except ValueError:
                pass
        src = Source(self.name, 'all', 'ALL')
        src.meta = min(max(pages, 1), MAX_PAGES)
        return [src]

    def fetch(self, sources, ctx):
        log().info('[douyu] %d 个来源', len(sources))
        rooms = fetch_dir(sources, ctx)
        out = []
        for rid, meta in rooms.items():
            out.append(Room(
                platform=self.name, rid=rid,
                title=meta['title'], nickname=meta['nickname'],
                url=PLAYER_BASE.format(rid),
                group=meta['group'] or self.name, avatar=meta['avatar']))
        log().info('[douyu] 完成: %d 在播房间（地址走 Worker 动态解析，未逐个取流）',
                   len(out))
        return out


platform = DouyuPlatform()
