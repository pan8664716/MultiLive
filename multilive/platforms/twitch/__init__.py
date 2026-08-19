"""Twitch 直播（匿名 GQL 批量列表，纯 HTTP，无浏览器）。

列表：gql.twitch.tv/gql 的 streams 查询（匿名可用，需网页版 Client-ID，
  2026-08 实测通过），first=30/页 + after 游标翻页；条目带
  broadcaster.login / displayName / game.name / previewImageURL。
播放地址：列表接口不带流地址（usher 需签名），m3u 统一写
  https://astar.cc.cd/twitch/<login> —— Worker 点播时实时解析。

m3u 语义：keep_stale=False，只保留此刻在播。
"""
import re
import time

from multilive.core import Room, Session, Source, fmt_exc, log
from multilive.platforms.base import Platform

NAME = 'twitch'
keep_stale = False

GQL_URL = 'https://gql.twitch.tv/gql'
CLIENT_ID = 'b31o4btkqth5bzbvr9ub2ovr79umhh'  # Twitch 网页版匿名 Client-ID（2026-08 实测）
PLAYER_BASE = 'https://astar.cc.cd/twitch/{}'
PAGE_SIZE = 30              # 服务端硬上限：first 只允许 1~30/页
DEFAULT_PAGES = 40          # 30/页 ≈ 1200 房间
MAX_PAGES = 120             # ≈ 3600 房间（再多靠翻页数控制）
PAGE_SLEEP = 0.5            # 翻页间隔（GQL 批量接口，但连续翻页会被限速）
REQUEST_TIMEOUT = 45        # 握手/响应超时调大，避免突发限速误判
RETRY_SLEEP = (2.0, 6.0)    # 单页失败重试退避
MAX_WORKERS = 3

QUERY = '''query Streams($first: Int, $after: Cursor) {
  streams(first: $first, after: $after) {
    edges { node { id title viewersCount type game { name }
                   broadcaster { id login displayName } previewImageURL } }
    pageInfo { hasNextPage endCursor }
  }
}'''

def fetch_page(sess, after):
    payload = {'query': QUERY,
               'variables': {'first': PAGE_SIZE, 'after': after}}
    last = None
    for attempt in range(3):
        try:
            _st, j, _ = sess.post_json(
                GQL_URL, payload, headers={'Client-Id': CLIENT_ID},
                referer='https://www.twitch.tv/directory',
                timeout=REQUEST_TIMEOUT)
            return ((j.get('data') or {}).get('streams') or {})
        except Exception as e:
            last = e
            if attempt < 2:
                time.sleep(RETRY_SLEEP[attempt])
    raise last

def fetch_list(sources, ctx):
    """顺序翻页抓在播流（30/页），返回 {login: Room 元数据}。"""
    pages = max(s.meta or DEFAULT_PAGES for s in sources)
    if ctx.pages_cap:
        pages = min(pages, ctx.pages_cap)
    rooms = {}
    after = None
    for page in range(1, pages + 1):
        try:
            data = fetch_page(Session(), after)
        except Exception as e:
            log().info('  [列表] 第%d页失败: %s', page, fmt_exc(e))
            break
        edges = data.get('edges') or []
        for edge in edges:
            node = edge.get('node') or {}
            broadcaster = node.get('broadcaster') or {}
            login = str(broadcaster.get('login') or '').strip().lower()
            if not login or login in rooms:
                continue
            game = node.get('game') or {}
            avatar = str(node.get('previewImageURL') or '').strip()
            avatar = avatar.replace('{width}x{height}', '320x180')
            rooms[login] = {
                'title': (node.get('title') or '').strip(),
                'nickname': (broadcaster.get('displayName') or login).strip(),
                'group': (game.get('name') or '').strip() or NAME,
                'avatar': avatar,
            }
        if not edges or not (data.get('pageInfo') or {}).get('hasNextPage'):
            break
        after = (data.get('pageInfo') or {}).get('endCursor')
        log().info('  [列表] 第%d页: %d 个, 累计 %d', page, len(edges),
                   len(rooms))
        time.sleep(PAGE_SLEEP)
    log().info('  [列表] %d页共 %d 个不重复直播间', pages, len(rooms))
    return rooms

class TwitchPlatform(Platform):
    """Twitch 平台：匿名 GQL 批量列表；播放地址走 Worker 动态解析。"""
    name = NAME
    keep_stale = keep_stale
    max_workers = MAX_WORKERS

    def parse(self, line):
        t = line.strip()
        m = re.match(r'https?://www\.twitch\.tv/directory(:\d+)?', t)
        if not m:
            m2 = re.fullmatch(r'ALL(:\d+)?', t.upper())
            if not m2:
                return []
            pages = DEFAULT_PAGES
            if m2.group(1):
                try:
                    pages = int(m2.group(1)[1:])
                except ValueError:
                    pass
            src = Source(self.name, 'list', 'ALL')
            src.meta = min(max(pages, 1), MAX_PAGES)
            return [src]
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
        log().info('[twitch] %d 个来源', len(sources))
        rooms = fetch_list(sources, ctx)
        out = []
        for login, meta in rooms.items():
            out.append(Room(
                platform=self.name, rid=login,
                title=meta['title'], nickname=meta['nickname'],
                url=PLAYER_BASE.format(login),
                group=meta['group'] or self.name, avatar=meta['avatar']))
        log().info('[twitch] 完成: %d 在播直播间（地址走 Worker 动态解析）',
                   len(out))
        return out

platform = TwitchPlatform()
