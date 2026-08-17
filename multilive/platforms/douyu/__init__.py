"""斗鱼直播。

抓取策略（2026-08 站点新签名协议已逆向，主链路纯 HTTP）：
  目录：GET gapi/rkc/directory/0_0/<page>（directory/all 同款接口，
    120 房间/页，含昵称/房间名/分类/封面）。

  播放地址（参考抖音「先取参数、再纯 HTTP」思路）：
    ① did：passport did 接口取 32 位 hex；失败则随机 28 位 hex + "1701"
    ② key：GET /wgapi/livenc/liveweb/websec/getEncryption?did=<did>
       服务端一次性下发 enc_data（base64 JSON，内含服务端算好的
       sign 与 op{did,ip,ts,ua}）以及 key / rand_str / enc_time /
       is_special / cpp（key 与 rand_str 为本地签名种子，expire_at 约 10 分钟）
    ③ auth：本地 MD5 链（"stream" 类型）
       u0 = rand_str；u_i = MD5(u_{i-1} + key) 共 enc_time 次
       auth = MD5(u_n + key + rid + ts)     （is_special=1 时省略 rid+ts）
    ④ POST /lapi/live/getH5PlayV1/<rid>，表单：
       enc_data / tt / did / auth / cdn= / ver=Douyu_new / rate=-1 /
       hevc=1 / fa=0 / ive=0
    ⑤ 直链 = rtmp_url + '/' + rtmp_live（flv，带 wsAuth/token，有时效）

  浏览器兜底：纯 HTTP 取参被风控时，跑 tools/douyu_warm.mjs（Patchright
  打开房间页取 did + getEncryption 结果），之后继续纯 HTTP 拉播放地址；
  结果缓存到 output/douyu_warm.json，未过期不重复开浏览器。

m3u 语义：keep_stale=False（斗鱼直链有签名时效，只留此刻在播）。
"""
import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

from multilive.core import Room, Session, Source, fmt_exc, log
from multilive.platforms.base import Platform

NAME = 'douyu'
keep_stale = False

DIR_API = 'https://www.douyu.com/gapi/rkc/directory/0_0/{}'
DID_API = 'https://passport.douyu.com/lapi/did/api/get?client_id=1&callback=cb'
KEY_API = 'https://www.douyu.com/wgapi/livenc/liveweb/websec/getEncryption?did={}'
PLAY_API = 'https://www.douyu.com/lapi/live/getH5PlayV1/{}'

DEFAULT_PAGES = 10
MAX_PAGES = 65
LIST_WORKERS = 5
PLAY_WORKERS = 5
KEY_REFRESH_LEAD = 120     # 剩余有效期小于该秒数就刷新 key
WARM_TIMEOUT = 180
CACHE_NAME = 'douyu_warm.json'


def _md5(s):
    return hashlib.md5(s.encode()).hexdigest()


def _random_did():
    return ''.join(random.choice('0123456789abcdef') for _ in range(28)) + '1701'


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


# ---------------- 加密参数（did + websec key）----------------

def fetch_did(sess):
    """优先 passport did 接口；失败退化为随机 did（实测同格式可被接受）。"""
    try:
        st, raw, _ = sess.get_text(DID_API, referer='https://www.douyu.com/')
        m = re.search(r'cb\((.*)\)\s*$', raw, re.S)
        j = json.loads(m.group(1))
        did = str((j.get('data') or {}).get('did') or '').lower()
        if re.fullmatch(r'[0-9a-f]{32}', did):
            return did
    except Exception as e:
        log().debug('did 接口异常(%s)，改用随机 did', fmt_exc(e))
    return _random_did()


def fetch_key(sess, did):
    """GET websec/getEncryption，返回服务端加密参数（含 enc_data 模板）。"""
    st, j, _ = sess.get_json(KEY_API.format(did),
                             referer='https://www.douyu.com/directory/all')
    data = j.get('data') or {}
    if j.get('error') != 0 or not data.get('enc_data'):
        raise RuntimeError(f'getEncryption 异常: {str(j)[:120]}')
    return {
        'did': did,
        'enc_data': data['enc_data'],
        'key': data.get('key') or '',
        'rand_str': data.get('rand_str') or '',
        'enc_time': int(data.get('enc_time') or 1),
        'is_special': int(data.get('is_special') or 0),
        'expire_at': int(data.get('expire_at') or 0),
        'cpp': data.get('cpp') or {},
    }


def warm_http():
    sess = Session()
    did = fetch_did(sess)
    bundle = fetch_key(sess, did)
    bundle['source'] = 'http'
    return bundle


def warm_browser(ctx):
    """浏览器取参兜底：tools/douyu_warm.mjs 输出同结构 JSON。"""
    script = os.path.join(ctx.project_root, 'tools', 'douyu_warm.mjs')
    if not (os.path.exists(script) and shutil.which('node')):
        raise RuntimeError('无 tools/douyu_warm.mjs 或 Node，浏览器兜底不可用')
    r = subprocess.run(['node', script], capture_output=True,
                       text=True, timeout=WARM_TIMEOUT)
    if r.returncode != 0:
        raise RuntimeError((r.stderr.strip() or r.stdout.strip())[-200:])
    j = json.loads(r.stdout)
    need = ('did', 'enc_data', 'key', 'rand_str')
    if not all(j.get(k) for k in need):
        raise RuntimeError('douyu_warm 输出字段不全: ' + str(j)[:120])
    j['enc_time'] = int(j.get('enc_time') or 1)
    j['is_special'] = int(j.get('is_special') or 0)
    j['expire_at'] = int(j.get('expire_at') or 0)
    j['source'] = 'browser'
    return j


def cache_path(ctx):
    return os.path.join(ctx.project_root, 'output', CACHE_NAME)


def load_cache(ctx):
    p = cache_path(ctx)
    try:
        with open(p, encoding='utf-8') as f:
            b = json.load(f)
        if (b.get('expire_at') or 0) - time.time() > KEY_REFRESH_LEAD:
            return b
    except Exception:
        pass
    return None


def save_cache(ctx, bundle):
    try:
        os.makedirs(os.path.dirname(cache_path(ctx)), exist_ok=True)
        with open(cache_path(ctx), 'w', encoding='utf-8') as f:
            json.dump(bundle, f, ensure_ascii=False)
    except Exception as e:
        log().debug('缓存 douyu_warm 失败: %s', fmt_exc(e))


def warm(ctx):
    """取 did + 加密 key：HTTP 优先，失败走浏览器，再写缓存。"""
    cached = load_cache(ctx)
    if cached:
        log().info('  [取参] 命中缓存(did=%s…，余 %.0fs)',
                   (cached.get('did') or '')[:8],
                   cached.get('expire_at', 0) - time.time())
        return cached
    try:
        b = warm_http()
        log().info('  [取参] 纯HTTP成功(did=%s…, %ss)',
                   b['did'][:8], b['source'])
    except Exception as e:
        log().warning('  [取参] HTTP失败(%s)，转浏览器兜底', fmt_exc(e))
        b = warm_browser(ctx)
        log().info('  [取参] 浏览器取参成功(did=%s…, %ss)',
                   b['did'][:8], b['source'])
    save_cache(ctx, b)
    return b


def compute_auth(bundle, rid, ts):
    """本地 MD5 链计算 getH5PlayV1 的 auth 参数。"""
    u = bundle['rand_str']
    key = bundle['key']
    for _ in range(bundle['enc_time']):
        u = _md5(u + key)
    o = '' if bundle['is_special'] == 1 else f'{rid}{ts}'
    return _md5(u + key + o)


def play_url(data):
    """从 getH5PlayV1 响应提取可播放直链。"""
    if not isinstance(data, dict):
        try:
            data = json.loads(data or '{}')
        except Exception:
            return ''
    rtmp = (data.get('rtmp_url') or '').rstrip('/')
    live = (data.get('rtmp_live') or '').lstrip('/')
    if rtmp and live:
        return f'{rtmp}/{live}'
    u = data.get('hls_url') or ''
    if u:
        return u
    u = data.get('player_1') or ''
    return u


class _SystemicError(RuntimeError):
    """站点级故障（鉴权/风控/签名失效），触发换 key 或熔断。"""


class _RoomError(RuntimeError):
    """房间级错误（未开播/无流等），仅跳过该房间。"""


def resolve_play(sess, bundle, rid):
    """纯 HTTP POST getH5PlayV1。返回 (url, raw_json) 或抛错。"""
    ts = int(time.time())
    auth = compute_auth(bundle, rid, ts)
    form = urllib.parse.urlencode({
        'enc_data': bundle['enc_data'],
        'tt': str(ts),
        'did': bundle['did'],
        'auth': auth,
        'cdn': '', 'ver': 'Douyu_new', 'rate': '-1',
        'hevc': '1', 'fa': '0', 'ive': '0',
    })
    st, raw, _ = sess.request(
        PLAY_API.format(rid), data=form,
        referer=f'https://www.douyu.com/{rid}',
        headers={'Content-Type': 'application/x-www-form-urlencoded',
                 'Origin': 'https://www.douyu.com'})
    try:
        j = json.loads(raw)
    except ValueError:
        raise RuntimeError(f'getH5PlayV1 非JSON(status={st}): {raw[:120]!r}')
    if j.get('error') != 0:
        code = j.get('error')
        msg = str(j.get('msg') or '')
        text = f'error={code} msg={msg}'.strip()[:120]
        if st == 403 or '鉴权' in msg or '签名' in msg \
                or code in (-1, -2, -3):
            raise _SystemicError(text)
        raise _RoomError(text)
    return play_url(j.get('data') or {}), j


class DouyuPlatform(Platform):
    """斗鱼平台：目录列表 + 播放地址主链路纯 HTTP，浏览器仅取参兜底。"""
    name = NAME
    keep_stale = keep_stale
    max_workers = PLAY_WORKERS

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
        if not rooms:
            return []

        try:
            bundle = warm(ctx)
        except Exception as e:
            log().warning('[douyu] 取加密参数失败(%s)，本轮跳过播放地址', fmt_exc(e))
            return []

        state = {'bundle': bundle, 'lock': threading.Lock(),
                 'aborted': False, 'sys_fail': 0, 'refreshed': False}
        out = []

        def get_bundle():
            """返回可用的加密参数；临近过期时刷新（HTTP 优先，浏览器兜底）。"""
            with state['lock']:
                b = state['bundle']
                if b['expire_at'] - time.time() > KEY_REFRESH_LEAD:
                    return b
                log().info('  [取参] key 临近过期，刷新…')
                try:
                    nb = warm_http()
                except Exception as e:
                    log().warning('  [取参] HTTP刷新失败(%s)，走浏览器', fmt_exc(e))
                    try:
                        nb = warm_browser(ctx)
                    except Exception:
                        nb = b  # 保底沿用旧 key（请求会失败并被计数熔断）
                state['bundle'] = nb
                save_cache(ctx, nb)
                return nb

        def work(rid):
            if state['aborted']:
                return None
            b = get_bundle()
            try:
                url, _j = resolve_play(Session(), b, rid)
                with state['lock']:
                    state['sys_fail'] = 0
                return url or None
            except _RoomError:
                return None  # 房间级错误：未开播/无流等，仅跳过
            except _SystemicError as e:
                msg = fmt_exc(e)
                with state['lock']:
                    state['sys_fail'] += 1
                    if state['sys_fail'] >= 3 and not state['refreshed']:
                        state['refreshed'] = True
                        refreshed = True
                    else:
                        refreshed = False
                    if state['sys_fail'] >= 6:
                        state['aborted'] = True
                        abort = True
                    else:
                        abort = False
                if refreshed:
                    log().warning('[douyu] 疑似鉴权异常(%s)，换 key 重试', msg[:60])
                    try:
                        with state['lock']:
                            nb = warm_http()
                            state['bundle'] = nb
                        save_cache(ctx, nb)
                        url, _j = resolve_play(Session(), get_bundle(), rid)
                        return url or None
                    except Exception:
                        pass
                if abort:
                    log().warning('[douyu] 播放接口持续异常(%s)，本轮停止解析'
                                  '（%d个房间已跳过）', msg[:60],
                                  len(rooms) - len(out))
                return None
            except Exception as e:
                log().info('  [房间] %s 网络异常: %s', rid, fmt_exc(e))
                return None

        with ThreadPoolExecutor(max_workers=PLAY_WORKERS) as ex:
            futs = {ex.submit(work, rid): rid for rid in rooms}
            for fut, rid in [(f, r) for f, r in futs.items()]:
                if state['aborted']:
                    for f in futs:
                        f.cancel()
                    break
                url = fut.result()
                if url:
                    meta = rooms[rid]
                    r = Room(platform=self.name, rid=rid,
                             title=meta['title'], nickname=meta['nickname'],
                             url=url, group=meta['group'], avatar=meta['avatar'])
                    out.append(r)
        for f in futs:
            if f.done() and not f.cancelled():
                try:
                    f.exception()
                except Exception:
                    pass
        log().info('[douyu] 完成: %d 房间（目录 %d 个，可播放 %d 个）',
                   len(out), len(rooms), len(out))
        return out


platform = DouyuPlatform()
