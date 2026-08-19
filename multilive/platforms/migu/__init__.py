"""咪咕视频 IPTV 直播列表（纯 HTTP，官方批量频道列表）。

列表：program-sc.miguvideo.com/live/v2/tv-data/ 官方频道列表接口——
  根 ID 返回分类(liveList) + 央视频道(dataList)，其余每个分类按
  vomsID 再拉频道列表(dataList)；频道带 pID / name / pics(台标)。
播放地址：playurl 需要逐频道 md5 盐值签名，不逐频道取流，m3u 统一写
  https://astar.cc.cd/migu/<pID> —— Worker 点播时实时解析。

m3u 语义：keep_stale=True——IPTV 频道稳定，接口波动时保留历史条目
（点播时由 Worker 实时解析，不依赖过期直链）。
"""
from concurrent.futures import ThreadPoolExecutor

from multilive.core import Room, Session, Source, fmt_exc, log
from multilive.platforms.base import Platform

NAME = 'migu'
keep_stale = True

ROOT_ID = '1ff892f2b5ab4a79be6e25b69d2f5d05'   # 分类根（该 ID 本身就是「央视」）
TV_DATA = 'https://program-sc.miguvideo.com/live/v2/tv-data/{}'
PLAYER_BASE = 'https://astar.cc.cd/migu/{}'
SKIP_CATEGORIES = {'热门'}                       # 与其他分类重复
MAX_WORKERS = 4

def fetch_body(sess, url):
    _st, j, _ = sess.get_json(url, referer='https://www.miguvideo.com/')
    return (j.get('body') or {})

def fetch_categories(sess):
    body = fetch_body(sess, TV_DATA.format(ROOT_ID))
    cats = body.get('liveList') or []
    return [c for c in cats
            if (c.get('name') or '').strip() not in SKIP_CATEGORIES], body

def fetch_channels(sess, voms_id):
    body = fetch_body(sess, TV_DATA.format(voms_id))
    return body.get('dataList') or []

def build_room(item, group):
    name = (item.get('name') or '').strip()
    pid = str(item.get('pID') or '').strip()
    if not pid or not name:
        return None
    pics = item.get('pics') or {}
    avatar = str(pics.get('highResolutionH')
                 or pics.get('lowResolutionH') or '').strip()
    return Room(platform=NAME, rid=pid, title=name, nickname='',
                url=PLAYER_BASE.format(pid),
                group=group or NAME, avatar=avatar)

def fetch_all(sources, ctx):
    cats, root_body = fetch_categories(Session())
    all_rooms = {}

    def _one(cat):
        voms = (cat.get('vomsID') or '').strip()
        if not voms:
            return []
        try:
            items = (root_body.get('dataList') or [] if voms == ROOT_ID
                     else fetch_channels(Session(), voms))
        except Exception as e:
            log().info('  [分类] %s 失败: %s', cat.get('name'), fmt_exc(e))
            return []
        gname = (cat.get('name') or '').strip() or NAME
        return [r for r in (build_room(it, gname) for it in items) if r]

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(_one, c): c.get('name') for c in cats}
        for fut, name in futs.items():
            try:
                rooms = fut.result()
            except Exception as e:
                log().warning('  [分类] %s 异常: %s', name, fmt_exc(e))
                continue
            for r in rooms:
                if r.rid not in all_rooms:
                    all_rooms[r.rid] = r
    log().info('  [列表] %d 个分类, %d 个不重复频道',
               len(cats), len(all_rooms))
    return list(all_rooms.values())

class MiguPlatform(Platform):
    """咪咕平台：官方频道列表纯 HTTP；播放地址走 Worker 动态解析。"""
    name = NAME
    keep_stale = keep_stale
    max_workers = MAX_WORKERS

    def parse(self, line):
        t = line.strip().upper()
        if t != 'ALL':
            return []
        return [Source(self.name, 'list', 'ALL')]

    def fetch(self, sources, ctx):
        log().info('[migu] %d 个来源', len(sources))
        if not any(s.kind == 'list' for s in sources):
            return []
        rooms = fetch_all(sources, ctx)
        log().info('[migu] 完成: %d 频道（地址走 Worker 动态解析）', len(rooms))
        return rooms

platform = MiguPlatform()
