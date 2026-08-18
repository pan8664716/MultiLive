"""B站直播（纯 HTTP，免登录免签名；播放地址不逐房间取流）。

来源：
  - 整站列表 https://live.bilibili.com/all（目录同款）
      room/v1/room/get_user_recommend 匿名可用，100 房间/页
  - 分区列表 https://live.bilibili.com/p/eden/area-tags?parentAreaId=X&areaId=Y
      room/v1/area/getRoomList 匿名可用（约 20 房间/页）
  - 单个直播间 https://live.bilibili.com/6 或 6
      room/v1/room/get_info（仅元数据：判断在播、取标题昵称）

播放地址：不逐房间调 playUrl（避免风控），m3u 里直接写
  https://astar.cc.cd/bilibili/<房间号> —— Worker 点播时实时解析。

m3u 语义：keep_stale=False，只保留在播房间。
"""
import re
import time
import urllib.error
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

from multilive.core import Room, Session, Source, log
from multilive.platforms.base import Platform

NAME = 'bilibili'
keep_stale = False

PLAYER_BASE = 'https://astar.cc.cd/bilibili/{}'
INFO_API = 'https://api.live.bilibili.com/room/v1/room/get_info'
REC_API = 'https://api.live.bilibili.com/room/v1/room/get_user_recommend'
AREA_LIST_API = 'https://api.live.bilibili.com/room/v1/area/getRoomList'
AREA_API = ('https://api.live.bilibili.com/xlive/web-room/v1/index/'
            'getRoomBaseInfo')
MAX_WORKERS = 3
DEFAULT_LIST_PAGES = 5
MAX_LIST_PAGES = 20
RETRY_CODES = (403, 412, 429)
RETRY_SLEEP = (2.0, 6.0)   # 首次重试/二次重试退避秒数


def get_json(sess, url, referer=None):
    """GET + 解析，412/403/429 等风控码短退避重试两次。"""
    last = None
    for attempt in range(3):
        try:
            return sess.get_json(url, referer=referer)[1]
        except urllib.error.HTTPError as e:
            last = e
            if e.code in RETRY_CODES and attempt < 2:
                wait = RETRY_SLEEP[attempt]
                log().info('  [retry] %s 被限速(%s)，%.1fs 后重试',
                           url.split('/')[-1][:40], e.code, wait)
                time.sleep(wait)
                continue
            raise
        except Exception as e:
            last = e
            break
    raise last


def fetch_room(sess, short_id):
    """单个来源房间 -> Room；不在播或解析失败返回 (None, 原因)。

    只取元数据（get_info），不再逐房间调播放地址接口；播放地址统一写
    Worker 解析地址，点播时由 Worker 侧实时解析。
    """
    try:
        j = get_json(sess, f'{INFO_API}?room_id={short_id}')
        info = j.get('data') or {}
        if not info.get('room_id') or info.get('live_status') != 1:
            return None, '未在播'
        rid = str(info['room_id'])
        return Room(
            platform=NAME, rid=rid,
            title=(info.get('title') or '').strip(),
            nickname=(info.get('uname') or '').strip(),
            url=PLAYER_BASE.format(rid),
            group=(info.get('area_name') or '').strip() or NAME,
            avatar=info.get('user_cover') or info.get('keyframe') or ''), None
    except Exception as e:
        log().info('  [房间] %s 解析失败: %s', short_id, e)
        return None, str(e)[:80]


def fetch_rec_page(sess, page):
    try:
        _st, j, _ = sess.get_json(f'{REC_API}?page={page}&page_size=100')
        return (j.get('data') or [])
    except Exception as e:
        log().info('  [列表] 第%d页失败: %s', page, e)
        return []


def fetch_area_page(sess, pa, aa, page):
    """单个分区一页房间（getRoomList，page_size 请求 100、服务端约给 20）。"""
    try:
        _st, j, _ = sess.get_json(
            f'{AREA_LIST_API}?parent_area_id={pa}&area_id={aa}'
            f'&page={page}&page_size=100',
            referer='https://live.bilibili.com/p/eden/area-tags')
        return (j.get('data') or [])
    except Exception as e:
        log().info('  [分区] parent=%s area=%s 第%d页失败: %s',
                   pa, aa, page, e)
        return []


def fetch_area(sess, pa, aa, pages):
    out = {}
    for page in range(1, pages + 1):
        for it in fetch_area_page(sess, pa, aa, page):
            rid = str(it.get('roomid') or '')
            if rid and rid not in out:
                out[rid] = it
    log().info('  [分区] parent=%s area=%s %d页: %d 个房间',
               pa, aa, pages, len(out))
    return out


def fetch_area_map(sess, uids):
    """按 uid 批量取一级分区名（get_user_recommend 不返回分类字段）。
    一次最多带 50 个 uid，几百房间只需几个请求。返回 {uid: 分区名}。
    """
    area = {}
    for i in range(0, len(uids), 50):
        chunk = uids[i:i + 50]
        if not chunk:
            continue
        try:
            _st, j, _ = sess.get_json(
                AREA_API + '?req_biz=web_room_componet&'
                + '&'.join(f'uids={u}' for u in chunk),
                referer='https://live.bilibili.com/all')
            for uid, info in ((j.get('data') or {}).get('by_uids') or {}).items():
                name = str((info or {}).get('area_name') or '').strip()
                if name:
                    area[str(uid)] = name
        except Exception as e:
            log().info('  [分区] 批量%d个uid失败: %s', len(chunk), e)
    return area


def build_room(item):
    """批量列表条目 -> Room（不调任何逐房间接口）。"""
    rid = str(item.get('roomid') or '')
    if not rid:
        return None
    group = str(item.get('area_v2_name') or item.get('area_name')
                or '').strip() or NAME
    return Room(
        platform=NAME, rid=rid,
        title=(item.get('title') or '').strip(),
        nickname=(item.get('uname') or '').strip(),
        url=PLAYER_BASE.format(rid),
        group=group,
        avatar=item.get('user_cover') or item.get('face') or '')


def fetch_all(sources, ctx):
    pages = max(s.meta or DEFAULT_LIST_PAGES for s in sources)
    if ctx.pages_cap:
        pages = min(pages, ctx.pages_cap)
    items = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(fetch_rec_page, Session(), p): p
                for p in range(1, pages + 1)}
        for fut in futs:
            for it in fut.result():
                rid = str(it.get('roomid') or '')
                if rid and rid not in items:
                    items[rid] = it
    log().info('  [列表] %d页共 %d 个不重复房间', pages, len(items))
    out = [r for r in (build_room(it) for it in items.values()) if r]
    # 列表接口不带分类，批量补一次分区名（不逐房间）
    uids = [str(it.get('uid')) for it in items.values() if it.get('uid')]
    if uids:
        area = fetch_area_map(Session(), uids)
        if area:
            log().info('  [分区] 批量获取 %d/%d 个房间分类', len(area),
                       len(items))
        for room in out:
            it = items.get(room.rid)
            uid = str(it.get('uid')) if it else ''
            if uid in area:
                room.group = area[uid]
    log().info('  [解析] %d 个在播房间（播放地址走 Worker 动态解析，未逐个取流）',
               len(out))
    return out


class BilibiliPlatform(Platform):
    """B站平台：整站列表 + 单直播间；播放地址不逐房间取流（统一 Worker 解析）。"""
    name = NAME
    keep_stale = keep_stale
    max_workers = MAX_WORKERS

    def parse(self, line):
        t = line.strip()
        if re.match(r'https?://live\.bilibili\.com/p/eden/area-tags', t):
            pages = DEFAULT_LIST_PAGES
            mm = re.search(r':(\d+)$', t)
            if mm:
                try:
                    pages = int(mm.group(1))
                except ValueError:
                    pass
                t = t[:mm.start()]
            q = urllib.parse.parse_qs(urllib.parse.urlparse(t).query)
            pa = (q.get('parentAreaId') or [''])[0]
            aa = (q.get('areaId') or [''])[0]
            if pa.isdigit() and aa.isdigit():
                src = Source(self.name, 'area', f'{pa}:{aa}')
                src.meta = min(max(pages, 1), MAX_LIST_PAGES)
                return [src]
        m = re.match(r'https?://live\.bilibili\.com/(\d+)', t)
        if m:
            return [Source(self.name, 'room', m.group(1))]
        m = re.match(r'https?://live\.bilibili\.com/all(:\d+)?', t)
        if m:
            pages = DEFAULT_LIST_PAGES
            if m.group(1):
                try:
                    pages = int(m.group(1)[1:])
                except ValueError:
                    pass
            src = Source(self.name, 'all', 'ALL')
            src.meta = min(max(pages, 1), MAX_LIST_PAGES)
            return [src]
        # 裸数字行避免误认（抖音也有纯数字房间），B站必须显式前缀或 URL
        return []

    def fetch(self, sources, ctx):
        log().info('[bilibili] %d 个来源', len(sources))
        all_srcs = [s for s in sources if s.kind == 'all']
        room_srcs = [s for s in sources if s.kind == 'room']
        area_srcs = [s for s in sources if s.kind == 'area']
        out = []
        if all_srcs:
            out.extend(fetch_all(all_srcs, ctx))
        if area_srcs:
            items = {}
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
                futs = {}
                for s in area_srcs:
                    pa, _sep, aa = s.target.partition(':')
                    pages = (s.meta if isinstance(s.meta, int) and s.meta
                             else DEFAULT_LIST_PAGES)
                    if ctx.pages_cap:
                        pages = min(pages, ctx.pages_cap)
                    futs[ex.submit(fetch_area, Session(), pa, aa, pages)] = s
                for fut in futs:
                    for rid, it in fut.result().items():
                        if rid not in items:
                            items[rid] = it
            out.extend(r for r in (build_room(it)
                                   for it in items.values()) if r)
            log().info('  [分区] 合计 %d 个不重复房间', len(items))
        rooms = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            for room, _err in ex.map(lambda r: fetch_room(Session(), r),
                                     [s.target for s in room_srcs]):
                if room:
                    out.append(room)
        log().info('[bilibili] 完成: %d 在播房间', len(out))
        return out


platform = BilibiliPlatform()
