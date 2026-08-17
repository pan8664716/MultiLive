"""YY 直播。

抓取策略：
  列表：频道页纯 HTTP 分页补齐。SSR 只内嵌第一页（dance 24/页、
    sing 30/页、pretty 60/页），页面 pageInfo 里有 totalCount/moduleId/
    biz，再用 /more/page.action 分页接口（pageSize=200）把剩余房间
    全部抓齐；实测 dancing≈170 / music≈400 / pretty≈150。
  播放地址：不逐个取流（避免风控）。m3u 里的地址直接写成
    https://astar.cc.cd/yy/<房间号> —— Worker 侧在点播时实时解析出
    最高画质 FLV 直链并 302，看哪个解析哪个。

m3u 语义：keep_stale=False，只保留在播房间。
"""
import re

from multilive.core import Room, Session, Source, fmt_exc, log
from multilive.platforms.base import Platform

NAME = 'yy'
keep_stale = False

BASE = 'https://www.yy.com/{}'
PLAYER_BASE = 'https://astar.cc.cd/yy/{}'
CAT_API = 'https://www.yy.com/more/page.action'
PAGE_SIZE = 200
LIST_WORKERS = 3

GROUP_MAP = {
    'dancing': '舞蹈',
    'pretty': '颜值',
    'music': '音乐',
    'sing': '音乐',
}


def _grab(bar, key):
    m = re.search(key + r'\s*:\s*(\d+)', bar)
    return int(m.group(1)) if m else 0


def _grab_str(bar, key):
    m = re.search(key + r"\s*:\s*'([^']*)'", bar)
    return m.group(1) if m else ''


def fetch_category(sess, cat):
    """SSR 第一页（含房间标题）+ 分页接口补齐 -> rooms dict。"""
    rooms = {}
    html = ''
    try:
        _st, html, _ = sess.get_text(BASE.format(cat),
                                     referer='https://www.yy.com/')
    except Exception as e:
        log().info('  [频道] %s 页面失败: %s', cat, fmt_exc(e))
        return rooms
    gm = re.search(r'data-stat-name="([^"]+)"', html)
    group = GROUP_MAP.get(cat) or (gm.group(1).strip() if gm else cat)
    # SSR 第一页（data-title 是真正的房间标题）
    for block in re.split(r'<li\b', html):
        m = re.search(r'data-url="/(\d+)/(\d+)\?(?:[^"]*?tempId=\d+[^"]*?)?"'
                      r'[^>]*?data-title="([^"]*)"', block)
        if not m:
            continue
        sid, ssid, title = m.groups()
        title = re.sub(r'\s*正在直播$', '', (title or '').strip())
        nm = re.search(r'<span class="intro">([^<]*)</span>', block)
        nick = (nm.group(1) if nm else '').strip()
        am = re.search(r'data-original="([^"]+)"', block)
        avatar = (am.group(1) if am else '').strip()
        if not sid:
            continue
        rooms[sid] = {
            'sid': sid, 'ssid': ssid or sid,
            'title': title, 'nickname': nick or title,
            'group': group, 'avatar': avatar,
        }
    # 分页接口补齐剩余房间（SSR 只含第一页，接口 JSON 无房间标题）
    pm = re.search(r'pageBar\s*:\s*\{([^}]*)\}', html)
    if pm:
        bar = pm.group(1)
        module_id = _grab(bar, 'moduleId')
        total = _grab(bar, 'totalCount')
        biz = _grab_str(bar, 'biz')
        sub = _grab_str(bar, 'subBiz')
        if biz and module_id and total:
            pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
            for page in range(1, pages + 1):
                api = (f'{CAT_API}?biz={biz}&subBiz={sub}&page={page}'
                       f'&moduleId={module_id}&pageSize={PAGE_SIZE}')
                try:
                    _st, j, _ = sess.get_json(api,
                                              referer=BASE.format(cat))
                except Exception as e:
                    log().info('  [频道] %s 第%d页失败: %s', cat, page,
                               fmt_exc(e))
                    continue
                for it in ((j.get('data') or {}).get('data') or []):
                    sid = str(it.get('sid') or '')
                    if not sid or sid in rooms:
                        continue
                    name = (it.get('name') or '').strip()
                    desc = (it.get('desc') or '').strip()
                    rooms[sid] = {
                        'sid': sid,
                        'ssid': str(it.get('ssid') or sid),
                        'title': re.sub(r'\s*正在直播$', '', desc),
                        'nickname': name or desc,
                        'group': group,
                        'avatar': (it.get('thumb2')
                                   or it.get('avatar') or '').strip(),
                    }
    log().info('  [频道] %s: %d 个在播房间', cat, len(rooms))
    return rooms


class YYPlatform(Platform):
    """YY 平台：列表纯 HTTP，播放地址走 Worker 动态解析。"""
    name = NAME
    keep_stale = keep_stale
    max_workers = LIST_WORKERS

    def parse(self, line):
        m = re.match(r'https?://www\.yy\.com/([a-zA-Z0-9_]+)/?$', line.strip())
        if m:
            return [Source(self.name, 'category', m.group(1))]
        return []

    def fetch(self, sources, ctx):
        log().info('[yy] %d 个来源', len(sources))
        cats = sorted({s.target for s in sources if s.kind == 'category'})
        rooms = {}
        for cat_rooms in self.parallel_map(
                lambda c: fetch_category(Session(), c), cats):
            for sid, meta in cat_rooms.items():
                if sid not in rooms:
                    rooms[sid] = meta
        log().info('  [列表] 共 %d 个不重复房间', len(rooms))
        if not rooms:
            return []
        out = []
        for sid, meta in rooms.items():
            out.append(Room(
                platform=self.name, rid=sid,
                title=meta['title'], nickname=meta['nickname'],
                url=PLAYER_BASE.format(sid), group=meta['group'] or self.name,
                avatar=meta.get('avatar') or ''))
        log().info('[yy] 完成: %d 在播房间（地址走 Worker 动态解析，未逐个取流）',
                   len(out))
        return out


platform = YYPlatform()
