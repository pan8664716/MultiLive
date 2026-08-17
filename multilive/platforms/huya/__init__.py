"""虎牙直播（纯 HTTP，2026-08 实测可播）。

两步纯 HTTP：
  1) cache.php?m=LiveList&do=getLiveListByPage —— 整站在播房间列表
     （120 房间/页），条目里 profileRoom 是真正的房间号
  2) 逐房间页 https://www.huya.com/<房间号> —— 解析内嵌
     hyPlayerConfig.stream.data[0]：
       gameLiveInfo(昵称/房间名/游戏) + gameStreamInfoList(CDN 直链)
  播放地址 = sFlvUrl + '/' + sStreamName + '.' + sFlvUrlSuffix + '?' + sFlvAntiCode
  （实测直接返回 FLV 头，PotPlayer/VLC 可播；无需额外签名计算）

m3u 语义：keep_stale=False，只保留此刻在播。
"""
import json
import re
from concurrent.futures import ThreadPoolExecutor

from multilive.core import Room, Session, Source, fmt_exc, log
from multilive.platforms.base import Platform

NAME = 'huya'
keep_stale = False

LIVE_LIST_API = 'https://www.huya.com/cache.php'
ROOM_URL = 'https://www.huya.com/{}'
DEFAULT_PAGES = 10        # 默认扫 10 页列表（1200 房间）
MAX_PAGES = 79
LIST_WORKERS = 5
ROOM_WORKERS = 5


def fetch_list_page(sess, page, total):
    url = (f'{LIVE_LIST_API}?m=LiveList&do=getLiveListByPage'
           f'&tagAll=0&page={page}')
    try:
        _st, j, _ = sess.get_json(url, referer='https://www.huya.com/l')
        return (j.get('data') or {}).get('datas') or []
    except Exception as e:
        log().info('  [列表] 第%d页失败: %s', page, fmt_exc(e))
        return []


def fetch_list(sources, ctx):
    """并发翻列表，去重房间号，返回 [(profileRoom, uid, nick, roomName, game)]。"""
    pages = max(s.meta or DEFAULT_PAGES for s in sources)
    if ctx.pages_cap:
        pages = min(pages, ctx.pages_cap)
    rooms = {}
    with ThreadPoolExecutor(max_workers=LIST_WORKERS) as ex:
        futs = {ex.submit(fetch_list_page, Session(), p, pages): p
                for p in range(1, pages + 1)}
        for fut in futs:
            for it in fut.result():
                rid = str(it.get('profileRoom') or '').strip()
                if rid and rid not in rooms:
                    rooms[rid] = (it.get('uid'), it.get('nick') or '',
                                  it.get('roomName') or '',
                                  it.get('gameFullName') or '')
    log().info('  [列表] %d页共 %d 个不重复房间', pages, len(rooms))
    return list(rooms.items())


def extract_stream(html, rid):
    """从房间页提取 (做法) 播放地址；在播且可解析返回 dict，否则 None。"""
    i = html.find('stream:')
    if i < 0:
        return None
    j = html.find('{', i)
    if j < 0:
        return None
    depth = 0
    for k in range(j, len(html)):
        if html[k] == '{':
            depth += 1
        elif html[k] == '}':
            depth -= 1
            if depth == 0:
                break
    try:
        stream = json.loads(html[j:k + 1])
    except Exception:
        return None
    d0 = (stream.get('data') or [{}])[0]
    gli = d0.get('gameLiveInfo') or {}
    gsl = d0.get('gameStreamInfoList') or []
    if not gsl:
        return None
    best = None
    for s in gsl:
        if s.get('sFlvUrl') and s.get('sStreamName'):
            best = s
            break
    if best is None:
        return None
    url = (f"{best['sFlvUrl']}/{best['sStreamName']}."
           f"{best.get('sFlvUrlSuffix') or 'flv'}?{best.get('sFlvAntiCode') or ''}")
    return {'rid': rid,
            'title': (gli.get('roomName') or gli.get('introduction') or '').strip(),
            'nickname': (gli.get('nick') or '').strip(),
            'url': url.replace('http://', 'https://'),
            'group': (gli.get('gameFullName') or '').strip() or NAME,
            'avatar': gli.get('avatar180') or gli.get('screenshot') or ''}


def fetch_room_page(sess, rid):
    try:
        _st, body, _ = sess.get_text(ROOM_URL.format(rid),
                                     referer='https://www.huya.com/l',
                                     accept='text/html,application/xhtml+xml')
        return extract_stream(body, rid)
    except Exception as e:
        log().info('  [房间] %s 失败: %s', rid, fmt_exc(e))
        return None


class HuyaPlatform(Platform):
    """虎牙平台（当前在 multilive/cli.py 的 DISABLED 中下线）。"""
    name = NAME
    keep_stale = keep_stale
    max_workers = ROOM_WORKERS

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
        with ThreadPoolExecutor(max_workers=ROOM_WORKERS) as ex:
            futs = {ex.submit(fetch_room_page, Session(), rid): rid
                    for rid, _meta in rooms}
            for fut in futs:
                r = fut.result()
                if r:
                    out.append(Room(platform=self.name, **r))
        log().info('[huya] 完成: %d 在播房间（%d 个未解析到流地址已跳过）',
                   len(out), len(rooms) - len(out))
        return out


platform = HuyaPlatform()
