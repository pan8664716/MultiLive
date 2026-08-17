"""抖音直播（源自 douyin-actions 方案，三级降级保持原样）。

抓取策略（每来源三级自动降级）：
  ① HTTP 接口：分类接口带 a_bogus 参数（服务端只校验存在、不校验取值），
     房间用 room/web/enter；全程无需浏览器
  ② 浏览器（可选兜底）：tools/browser_fetch_douyin.mjs（Patchright），
     接口被 IP 风控时滚动加载+拦截签名接口
  ③ HTTP 页面解析：内嵌 RSC 数据（self.__pace_f.push），约 15 个置顶房间

m3u 语义：keep_stale=True——旧房间保留并用 pages.dev 动态地址兜底，
本轮在播房间写 CDN 直链并置顶。
"""
import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

from multilive.core import Room, Session, Source, fmt_exc, log
from multilive.platforms.base import Platform

NAME = 'douyin'
keep_stale = True

MAX_PAGES = 14           # 每分类最多 14 页（15/页 ≈ 210 房间）
PAGE_SLEEP = 1.2         # 分页请求间隔，避免触发风控
RETRY_SLEEP = 2.0
CATEGORY_RETRY = 3
MAX_WORKERS = 5          # 来源级并发
BROWSER_TIMEOUT = 300

# 分类接口只校验参数存在：188 位固定值实测全平台通用
A_BOGUS_PARAM = 'a' * 188

CATEGORY_NAMES = {
    '1010014': '英雄联盟', '1010045': '王者荣耀', '1010055': '金铲铲之战',
    '1010350': '魔兽争霸3', '1010032': '和平精英', '1011032': '三角洲行动',
    '1010092': '地下城与勇士',
    '3': '单机游戏', '1': '射击游戏', '2': '竞技游戏',
    '105': '舞蹈', '106': '文化', '107': '生活', '108': '运动',
    '102': '音乐', '104': '二次元',
}

CDN_QUALITY = ['FULL_HD1', 'HD1', 'SD1', 'SD2']
_browser_lock = threading.Lock()


def split_category(path):
    seg = [s for s in path.split('_') if s]
    if len(seg) < 2:
        raise ValueError(f'无法识别分类路径: {path}')
    return seg[-1], seg[-2]


def api_params(partition, ptype, offset, count=15):
    return {
        'aid': '6383', 'app_name': 'douyin_web', 'live_id': '1',
        'device_platform': 'web', 'language': 'zh-CN',
        'cookie_enabled': 'true', 'screen_width': '1280', 'screen_height': '720',
        'browser_language': 'zh-CN', 'browser_platform': 'Windows',
        'browser_name': 'Chrome', 'browser_version': '151.0.0.0',
        'os_name': 'Windows', 'os_version': '10',
        'count': str(count), 'offset': str(offset),
        'partition': partition, 'partition_type': ptype, 'req_from': '2',
        'a_bogus': A_BOGUS_PARAM,
    }


def check_risk(headers, body, where):
    if 'bdturing-verify' in headers or not body:
        raise RuntimeError(
            f'触发风控({headers.get("bdturing-verify", "empty body")}) @ {where}')


def extract_cdn_url(room):
    """从 stream_url 提取最高清 CDN 直链（hls 优先，其次 flv，转 https）。"""
    su = (room or {}).get('stream_url') or {}
    hls = su.get('hls_pull_url_map') or {}
    flv = su.get('flv_pull_url') or {}
    for q in CDN_QUALITY:
        u = hls.get(q) or ''
        if u:
            return u.replace('http://', 'https://')
    for q in CDN_QUALITY:
        u = flv.get(q) or ''
        if u:
            return u.replace('http://', 'https://')
    u = su.get('hls_pull_url') or ''
    return u.replace('http://', 'https://') or None


def _avatar_from(av):
    if isinstance(av, dict):
        ul = av.get('url_list') or []
        return str(ul[0]) if ul else ''
    return av if isinstance(av, str) else ''


def parse_category_item(it):
    room = it.get('room') or {}
    owner = room.get('owner') or it.get('owner') or {}
    rid = str(it.get('web_rid') or room.get('web_rid') or room.get('webRid') or '').strip()
    if not (rid.isdigit() and 6 <= len(rid) <= 15):
        return None
    avatar = ''
    for av in (it.get('avatar'), owner.get('avatar_thumb'), owner.get('avatar'),
               room.get('cover')):
        avatar = _avatar_from(av)
        if avatar:
            break
    return {'rid': rid,
            'title': (room.get('title') or '').strip(),
            'nickname': (owner.get('nickname') or owner.get('nick_name') or '').strip(),
            'avatar': avatar,
            'url': extract_cdn_url(room)}


def http_fetch_category(sess, path, pages_limit=MAX_PAGES):
    partition, ptype = split_category(path)
    last_err = ''
    for attempt in range(CATEGORY_RETRY):
        if attempt:
            time.sleep(RETRY_SLEEP)
        rooms = []
        try:
            for page in range(pages_limit):
                offset = page * 15
                url = ('https://live.douyin.com/webcast/web/partition/detail/room/v2/?'
                       + urllib.parse.urlencode(api_params(partition, ptype, offset)))
                st, body, hdrs = sess.get(
                    url, referer='https://live.douyin.com/categorynew/' + path)
                check_risk(hdrs, body, f'分类接口 p{page}')
                j = json.loads(body)
                items = (j.get('data') or {}).get('data') or []
                for it in items:
                    r = parse_category_item(it)
                    if r:
                        rooms.append(r)
                if len(items) < 15:
                    break
                time.sleep(PAGE_SLEEP)
            if rooms:
                return rooms
            last_err = '空数据'
        except Exception as e:
            last_err = fmt_exc(e)
        log().info('  [接口] %s 第%d次尝试%s, 重试', path, attempt + 1,
                   '为空' if not rooms else f'失败({last_err[:60]})')
    raise RuntimeError(f'分类接口重试{CATEGORY_RETRY}次仍失败: {last_err}')


def http_fetch_room(sess, rid):
    sess.get(f'https://live.douyin.com/{rid}', referer='https://www.google.com/')
    params = {
        'aid': '6383', 'app_name': 'douyin_web', 'live_id': '1',
        'device_platform': 'web', 'language': 'zh-CN', 'enter_from': 'link_share',
        'cookie_enabled': 'true', 'screen_width': '1280', 'screen_height': '720',
        'browser_language': 'zh-CN', 'browser_platform': 'Windows',
        'browser_name': 'Chrome', 'browser_version': '151.0.0.0',
        'os_name': 'Windows', 'os_version': '10',
        'web_rid': rid, 'room_id_str': '', 'enter_source': '',
        'is_need_double_stream': 'false', 'insert_task_id': '', 'live_reason': '',
    }
    url = 'https://live.douyin.com/webcast/room/web/enter/?' + urllib.parse.urlencode(params)
    st, body, hdrs = sess.get(
        url, referer=f'https://live.douyin.com/{rid}',
        accept='application/json, text/plain, */*')
    check_risk(hdrs, body, 'enter 接口')
    j = json.loads(body)
    d0 = (j.get('data') or {}).get('data') or []
    if not d0 or not d0[0]:
        return []
    d = d0[0]
    user = d.get('owner') or d.get('user') or {}
    avatar = _avatar_from(user.get('avatar_thumb') or user.get('avatar') or {})
    return [{'rid': rid,
             'title': (d.get('title') or '').strip(),
             'nickname': (user.get('nickname') or user.get('nick_name') or '').strip(),
             'avatar': avatar,
             'url': extract_cdn_url(d)}]


def extract_category_names(blob):
    """从 RSC categoryData 分类树提取 {(type, id): 类目名}。"""
    i = blob.find('"categoryData":')
    if i < 0:
        return {}
    j = blob.find('[', i)
    if j < 0:
        return {}
    depth = 0
    arr = None
    for k in range(j, len(blob)):
        c = blob[k]
        if c == '[':
            depth += 1
        elif c == ']':
            depth -= 1
            if depth == 0:
                try:
                    arr = json.loads(blob[j:k + 1])
                except Exception:
                    arr = None
                break
    if not arr:
        return {}
    out = {}

    def walk(nodes):
        for n in nodes:
            part = n.get('partition') or {}
            tid = str(part.get('id_str') or '')
            ty = part.get('type')
            if tid:
                out.setdefault((ty, tid), (part.get('title') or '').strip())
            walk(n.get('sub_partition') or [])

    walk(arr)
    return out


def category_group_name(path, names=None):
    seg = [s for s in path.split('_') if s]
    pairs = []
    for a, b in zip(seg[::2], seg[1::2]):
        pairs.append((int(a), b))
    if not pairs:
        return NAME
    if names:
        for ty, pid in reversed(pairs):
            t = names.get((ty, pid))
            if t:
                return t
    return CATEGORY_NAMES.get(pairs[-1][1], NAME)


def parse_page_html(html):
    """解析页面内嵌 RSC 数据（self.__pace_f.push 块）。"""
    parts = []
    for m in re.finditer(r'self\.__pace_f\.push\(\[1,"', html):
        s = m.end()
        e = html.find('"])</script>', s)
        if e < 0:
            break
        try:
            parts.append(json.loads('"' + html[s:e] + '"'))
        except Exception:
            continue
    blob = ''.join(parts)
    if not blob:
        raise RuntimeError('页面中未找到 RSC 数据')

    def extract_obj(idx):
        depth = 0
        start = idx
        for i in range(idx - 1, -1, -1):
            c = blob[i]
            if c == '}':
                depth += 1
            elif c == '{':
                if depth == 0:
                    start = i
                    break
                depth -= 1
        depth = 0
        for i in range(start, len(blob)):
            c = blob[i]
            if c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    return start, i + 1
        return None

    rooms, seen = [], set()
    for m in re.finditer(r'"web_rid":"(\d{6,15})"', blob):
        rid = m.group(1)
        if rid in seen:
            continue
        span = extract_obj(m.start())
        if not span:
            continue
        try:
            obj = json.loads(blob[slice(*span)])
        except Exception:
            continue
        rm = obj.get('room') or obj
        owner = rm.get('owner') or obj.get('owner') or obj.get('user') or {}
        avatar = _avatar_from(owner.get('avatar_thumb') or owner.get('avatar') or {})
        rooms.append({'rid': rid,
                      'title': (rm.get('title') or '').strip(),
                      'nickname': (owner.get('nickname') or owner.get('nick_name') or '').strip(),
                      'avatar': avatar,
                      'url': extract_cdn_url(rm)})
        seen.add(rid)
    return rooms, extract_category_names(blob)


def http_fetch_page(kind, target, sess):
    url = (f'https://live.douyin.com/categorynew/{target}' if kind == 'category'
           else f'https://live.douyin.com/{target}')
    referer = 'https://www.google.com/' if kind == 'room' else None
    _st, body, _hdrs = sess.get(url, referer=referer)
    return parse_page_html(body.decode('utf-8', 'ignore'))


def browser_script(ctx):
    p = os.path.join(ctx.project_root, 'tools', 'browser_fetch_douyin.mjs')
    return p if os.path.exists(p) else ''


def browser_fetch(kind, target, ctx):
    """浏览器兜底：tools/browser_fetch_douyin.mjs（Patchright）。"""
    script = browser_script(ctx)
    if not script or not shutil.which('node'):
        raise RuntimeError('缺少 Node 或 tools/browser_fetch_douyin.mjs，跳过浏览器兜底')
    url = (f'https://live.douyin.com/categorynew/{target}' if kind == 'category'
           else f'https://live.douyin.com/{target}')
    with _browser_lock:   # 多来源并发时浏览器实例串行
        log().info('  [浏览器] %s 滚动加载中...', url)
        r = subprocess.run(['node', script, url], capture_output=True,
                           text=True, timeout=BROWSER_TIMEOUT)
    if r.returncode != 0:
        raise RuntimeError('浏览器兜底失败: ' + (r.stderr.strip() or r.stdout.strip())[-300:])
    rooms = json.loads(r.stdout)
    if not isinstance(rooms, list):
        raise RuntimeError('浏览器兜底返回格式错误')
    return rooms


def fetch_source(kind, target, sess, has_http, ctx, pages_limit=MAX_PAGES):
    """单来源三级抓取，返回 (rooms, method, group)。"""
    group = category_group_name(target) if kind == 'category' else NAME
    if has_http:
        try:
            rooms = (http_fetch_category(sess, target, pages_limit) if kind == 'category'
                     else http_fetch_room(sess, target))
            if rooms:
                return rooms, '接口', group
        except Exception as e:
            log().info('  [接口] %s: %s', target, fmt_exc(e))
    else:
        log().info('  [接口] ttwid 初始化失败，跳过接口层')

    if kind == 'category':
        try:
            rooms = browser_fetch(kind, target, ctx)
            if rooms:
                return rooms, '浏览器', group
        except Exception as e:
            log().info('  [浏览器] %s: %s', target, fmt_exc(e))
        try:
            rooms, names = http_fetch_page(kind, target, sess)
            if rooms:
                return rooms, '页面', category_group_name(target, names)
        except Exception as e:
            log().info('  [页面] %s: %s', target, fmt_exc(e))
        raise RuntimeError('接口/浏览器/页面 三级均失败')

    try:
        rooms, _ = http_fetch_page(kind, target, sess)
        if rooms:
            return rooms, '页面', group
    except Exception as e:
        log().info('  [页面] %s: %s', target, fmt_exc(e))
    rooms = browser_fetch(kind, target, ctx)
    return rooms, '浏览器', group


def _warm_session():
    """注册 ttwid：访问分类页拿临时 cookie，再调 union register 升级。"""
    sess = Session()
    sess.get('https://live.douyin.com/categorynew/4_105')
    st, _j, _h = sess.post_json(
        'https://ttwid.bytedance.com/ttwid/union/register/',
        {'region': 'cn', 'aid': 6383, 'needFid': False,
         'service': 'live.douyin.com',
         'migrate_info': {'tier': '', 'from_model': 'pc'}},
        accept='application/json')
    return st, sess


class DouyinPlatform(Platform):
    """抖音平台：接口/浏览器/页面三级降级，历史房间用 pages.dev 兜底。"""
    name = NAME
    keep_stale = keep_stale
    max_workers = MAX_WORKERS

    def parse(self, line):
        """支持：直播间页 / 分类页 / 纯房间号。"""
        t = line.strip()
        m = re.match(r'https?://live\.douyin\.com/categorynew/([\d_]+)', t)
        if m:
            return [Source(self.name, 'category', m.group(1))]
        m = re.match(r'https?://live\.douyin\.com/(\d+)', t)
        if m:
            return [Source(self.name, 'room', m.group(1))]
        if re.fullmatch(r'\d{6,15}', t):
            return [Source(self.name, 'room', t)]
        return []

    def fetch(self, sources, ctx):
        log().info('[douyin] %d 个来源', len(sources))
        has_http = True
        try:
            st, master = _warm_session()
            log().info('[douyin] ttwid 初始化完成, status=%s', st)
            if st != 200:
                has_http = False
        except Exception as e:
            has_http = False
            log().warning('[douyin] ttwid 初始化失败(%s)，跳过接口层', fmt_exc(e))
            master = Session()

        pages_limit = MAX_PAGES
        if ctx.pages_cap:
            pages_limit = min(pages_limit, ctx.pages_cap)

        def _one(kind, target):
            # 每个 worker 用独立 Session（复制 master cookie），避免线程竞争
            shard = Session()
            for c in master.cj:
                shard.cj.set_cookie(c)
            try:
                rooms, method, group = fetch_source(
                    kind, target, shard, has_http, ctx, pages_limit)
                return rooms, method, group, None
            except Exception as e:
                return None, None, None, fmt_exc(e)

        tasks = [(s.kind, s.target) for s in sources]
        results = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            for r in ex.map(lambda t: _one(*t), tasks):
                results.append(r)

        out = []
        oks, fails = 0, 0
        for (rooms, method, group, err), src in zip(results, sources):
            if err is not None or not rooms:
                fails += 1
                log().warning('[douyin] 全部失败 %s: %s', src.target,
                              err or '空数据')
                continue
            oks += 1
            log().info('[douyin] [%s] %s: %d 个, group="%s"', method,
                       src.target, len(rooms), group)
            for r in rooms:
                out.append(Room(platform=self.name, rid=r['rid'], title=r['title'],
                                nickname=r['nickname'], avatar=r['avatar'],
                                url=r['url'] or '', group=group))
        log().info('[douyin] 完成: %d/%d 来源成功, %d 房间', oks, len(sources), len(out))
        return out

    def fallback_url(self, room):
        return f'https://douyin-m3u8.pages.dev/room/{room.rid}'


platform = DouyinPlatform()
