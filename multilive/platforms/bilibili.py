"""B站直播（纯 HTTP，免登录免签名）。

来源：
  - 整站列表 https://live.bilibili.com/all（目录同款）
      room/v1/room/get_user_recommend 匿名可用，100 房间/页
  - 单个直播间 https://live.bilibili.com/6 或 6
      room/v1/room/get_info + room/v1/Room/playUrl

m3u 语义：keep_stale=False，只保留在播房间。
"""
import re
from concurrent.futures import ThreadPoolExecutor

from multilive.config import Source
from multilive.core import Room, Session, log

NAME = 'bilibili'
keep_stale = False

INFO_API = 'https://api.live.bilibili.com/room/v1/room/get_info'
PLAY_API = 'https://api.live.bilibili.com/room/v1/Room/playUrl'
REC_API = 'https://api.live.bilibili.com/room/v1/room/get_user_recommend'
MAX_WORKERS = 5
DEFAULT_LIST_PAGES = 5
MAX_LIST_PAGES = 20


def parse(line):
    t = line.strip()
    m = re.match(r'https?://live\.bilibili\.com/(\d+)', t)
    if m:
        return [Source(NAME, 'room', m.group(1))]
    m = re.match(r'https?://live\.bilibili\.com/all(:\d+)?', t)
    if m:
        pages = DEFAULT_LIST_PAGES
        if m.group(1):
            try:
                pages = int(m.group(1)[1:])
            except ValueError:
                pass
        src = Source(NAME, 'all', 'ALL')
        src.meta = min(max(pages, 1), MAX_LIST_PAGES)
        return [src]
    # 裸数字行避免误认（抖音也有纯数字房间），B站必须显式前缀或 URL
    return []


def fetch_room(sess, short_id):
    """返回 Room；不在播或解析失败返回 None。"""
    _st, j, _ = sess.get_json(f'{INFO_API}?room_id={short_id}')
    info = j.get('data') or {}
    if not info.get('room_id') or info.get('live_status') != 1:
        return None, '未在播'
    rid = str(info['room_id'])
    _st2, pj, _ = sess.get_json(f'{PLAY_API}?cid={rid}&quality=0&platform=web')
    durl = ((pj.get('data') or {}).get('durl')) or []
    if not durl or not durl[0].get('url'):
        return None, '无播放地址'
    return Room(
        platform=NAME, rid=rid,
        title=(info.get('title') or '').strip(),
        nickname=(info.get('uname') or '').strip(),
        url=durl[0]['url'],
        group=(info.get('area_name') or '').strip() or NAME,
        avatar=info.get('user_cover') or info.get('keyframe') or ''), None


def fetch_rec_page(sess, page):
    try:
        _st, j, _ = sess.get_json(f'{REC_API}?page={page}&page_size=100')
        return (j.get('data') or [])
    except Exception as e:
        log().info('  [列表] 第%d页失败: %s', page, e)
        return []


def _resolve_room(sess, item):
    """列表条目 -> Room（在播且有地址才返回）。"""
    rid = str(item.get('roomid') or '')
    if not rid:
        return None
    _st, pj, _ = sess.get_json(f'{PLAY_API}?cid={rid}&quality=0&platform=web')
    durl = ((pj.get('data') or {}).get('durl')) or []
    if not durl or not durl[0].get('url'):
        return None
    return Room(
        platform=NAME, rid=rid,
        title=(item.get('title') or '').strip(),
        nickname=(item.get('uname') or '').strip(),
        url=durl[0]['url'],
        group='B站',
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
    out = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        for room in ex.map(lambda it: _resolve_room(Session(), it),
                           list(items.values())):
            if room:
                out.append(room)
    log().info('  [解析] %d 个在播可播', len(out))
    return out


def fetch(sources, ctx):
    log().info('[bilibili] %d 个来源', len(sources))
    all_srcs = [s for s in sources if s.kind == 'all']
    room_srcs = [s for s in sources if s.kind == 'room']
    out = []
    if all_srcs:
        out.extend(fetch_all(all_srcs, ctx))
    rooms = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        for room, _err in ex.map(lambda r: fetch_room(Session(), r),
                                 [s.target for s in room_srcs]):
            if room:
                out.append(room)
    log().info('[bilibili] 完成: %d 在播房间', len(out))
    return out
