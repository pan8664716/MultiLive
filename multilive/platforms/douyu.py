"""斗鱼直播。

列表：纯 HTTP —— https://www.douyu.com/gapi/rkc/directory/0_0/<page>
（directory/all 页面同款接口，120 房间/页，含昵称/房间名/分类/封面）。

播放地址：当前版本（2026-08 实测）已被站点签名策略锁死：
  - betard 接口不再下发 rtmp_url；
  - getH5Play 需要 homeH5Enc 动态下发的 42KB 混淆 JS 签名，
    纯 Python 无法复刻；Node 可执行签名 JS，但接口仍返回 403「鉴权失败」，
    说明还差播放器请求中的额外标识（待跟进站点改版）。
因此默认只收集「目录房间元数据」并尝试 Node 解析；解析不到的房间
不写入 m3u（不会让整体失败）。站点签名策略松动后，只需调整
tools/douyu_play.mjs 或本模块的解析函数即可恢复。

m3u 语义：keep_stale=False（斗鱼直链有签名时效，只留此刻在播）。
"""
import os
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor

from multilive.config import Source
from multilive.core import Room, Session, fmt_exc, log

NAME = 'douyu'
keep_stale = False

DIR_API = 'https://www.douyu.com/gapi/rkc/directory/0_0/{}'
DEFAULT_PAGES = 10
MAX_PAGES = 65
LIST_WORKERS = 5
PLAY_WORKERS = 5


def parse(line):
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
    src = Source(NAME, 'all', 'ALL')
    src.meta = min(max(pages, 1), MAX_PAGES)
    return [src]


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


def node_available(ctx):
    script = os.path.join(ctx.project_root, 'tools', 'douyu_play.mjs')
    return script if (os.path.exists(script) and shutil.which('node')) else ''


def resolve_play(ctx, rid):
    """调用 tools/douyu_play.mjs 解析播放地址。返回 dict(rid,url) 或 None。"""
    script = node_available(ctx)
    if not script:
        raise RuntimeError('无 Node，跳过斗鱼播放地址解析')
    r = subprocess.run(['node', script, rid], capture_output=True,
                       text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError((r.stderr.strip() or r.stdout.strip())[-160:])
    j = json_loads(r.stdout)
    return j


def fetch(sources, ctx):
    log().info('[douyu] %d 个来源', len(sources))
    rooms = fetch_dir(sources, ctx)
    script = node_available(ctx)
    if not script:
        log().warning('[douyu] 未检测到 Node 或 tools/douyu_play.mjs，'
                      '仅收集目录元数据，播放地址待 Node 解析')
        return []

    # 解析播放地址（5 并发）；若站点整体拒绝（鉴权失败），快速停止避免空转
    out = []
    aborted = [False]
    with ThreadPoolExecutor(max_workers=PLAY_WORKERS) as ex:
        futs = {ex.submit(resolve_play, ctx, rid): rid for rid in rooms}
        for fut, rid in [(f, r) for f, r in futs.items()]:
            if aborted[0]:
                for f in futs:
                    f.cancel()
                break
            try:
                pr = fut.result()
                if pr and pr.get('url'):
                    meta = rooms[rid]
                    r = Room(platform=NAME, rid=rid,
                             title=meta['title'], nickname=meta['nickname'],
                             url=pr['url'].replace('http://', 'https://'),
                             group=meta['group'], avatar=meta['avatar'])
                    out.append(r)
            except RuntimeError as e:
                msg = fmt_exc(e)
                if '鉴权失败' in msg or '403' in msg:
                    log().warning('[douyu] 播放接口被站点拒绝(%s)，'
                                  '本轮停止解析（房间已跳过）', msg[:60])
                    aborted[0] = True
                else:
                    log().info('[douyu] 房间 %s 解析失败: %s', rid, msg)
    # 清理未消费的 Future，避免解释器退出时打印 "exception was never retrieved"
    for f in futs:
        if f.done() and not f.cancelled():
            try:
                f.exception()
            except Exception:
                pass
    log().info('[douyu] 完成: %d 房间（目录 %d 个，可播放 %d 个）',
               len(out), len(rooms), len(out))
    return out


def json_loads(s):
    import json
    return json.loads(s)
