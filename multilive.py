#!/usr/bin/env python3
"""MultiLive 统一入口：多平台直播 m3u 聚合更新。

用法：
  python3 multilive.py                   # 按 sources.txt 抓取并写 multilive.m3u
  python3 multilive.py --dry-run         # 只打印统计，不写文件
  python3 multilive.py --platform douyin # 只跑指定平台（逗号分隔）
  python3 multilive.py --pages 10        # 限制单来源翻页数（快手/抖音分类）
  python3 multilive.py --verbose         # 控制台输出 DEBUG 日志

输出：
  multilive.m3u          聚合播放列表（本轮置顶 + 增量去重）
  <平台>_live.m3u        各平台独立列表（douyin/kuaishou/bilibili/douyu/huya…）
  out/status.json        每次运行的机器可读摘要
  out/run.log            运行日志（滚动保留 3 份）
"""
import argparse
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from logging.handlers import RotatingFileHandler
from types import SimpleNamespace

from multilive import __version__
from multilive.config import discover_platforms, load_sources
from multilive.core import fmt_exc, log
from multilive.m3u import merge, read_existing, write_m3u, write_status

ROOT = os.path.dirname(os.path.abspath(__file__))
SOURCES_PATH = os.path.join(ROOT, 'sources.txt')
MERGED_PATH = os.path.join(ROOT, 'multilive.m3u')
OUT_DIR = os.path.join(ROOT, 'out')
LOG_PATH = os.path.join(OUT_DIR, 'run.log')


def setup_logging(verbose):
    fmt = logging.Formatter('[%(asctime)s] %(levelname)s %(message)s',
                            datefmt='%H:%M:%S')
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    root.handlers.clear()

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(fmt)
    root.addHandler(console)

    os.makedirs(OUT_DIR, exist_ok=True)
    fileh = RotatingFileHandler(LOG_PATH, maxBytes=1_000_000, backupCount=3,
                                encoding='utf-8')
    fileh.setLevel(logging.DEBUG)
    fileh.setFormatter(fmt)
    root.addHandler(fileh)


def parse_args(argv):
    p = argparse.ArgumentParser(description='MultiLive 多平台直播 m3u 聚合器')
    p.add_argument('--dry-run', action='store_true', help='只打印统计，不写文件')
    p.add_argument('--platform', default='', help='只跑指定平台，逗号分隔')
    p.add_argument('--pages', type=int, default=0,
                   help='限制单来源翻页数上限（默认跟随各平台配置）')
    p.add_argument('--sources', default=SOURCES_PATH, help='来源配置文件路径')
    p.add_argument('--verbose', action='store_true', help='控制台输出 DEBUG')
    p.add_argument('--version', action='version', version=f'MultiLive {__version__}')
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv or sys.argv[1:])
    setup_logging(args.verbose)
    log().info('MultiLive v%s 启动', __version__)

    only = [s.strip() for s in args.platform.split(',') if s.strip()] or None
    sources = load_sources(args.sources, only_platforms=only)
    if not sources:
        raise SystemExit('sources.txt 没有可用的来源，请先配置（见 README）')
    platforms = discover_platforms()
    ctx = SimpleNamespace(project_root=ROOT, pages_cap=args.pages or 0)

    # 平台级并发：每个平台独立抓取，内部再自行并发
    t0 = time.time()
    new_rooms = []
    per_platform = {}
    with ThreadPoolExecutor(max_workers=len(sources)) as ex:
        futs = {}
        for name, src_list in sources.items():
            futs[ex.submit(platforms[name].fetch, src_list, ctx)] = name
        for fut, name in futs.items():
            try:
                rooms = fut.result()
                per_platform[name] = rooms
                new_rooms.extend(rooms)
            except Exception as e:
                log().error('[%s] 平台抓取失败: %s', name, fmt_exc(e))

    keep_stale = {name: getattr(platforms[name], 'keep_stale', False)
                  for name in sources}
    fallback_fn = getattr(platforms.get('douyin'), 'fallback_url', None)
    existing = read_existing(MERGED_PATH)
    merged, stats = merge(existing, new_rooms, keep_stale, fallback_fn)

    counts = {name: len(rooms) for name, rooms in per_platform.items()}
    log().info('抓取统计: %s', counts)
    log().info('合并统计: 新增=%d 刷新=%d 保留历史=%d 丢弃失效=%d 合计=%d',
               stats['added'], stats['refreshed'], stats['kept_stale'],
               stats['dropped_stale'], len(merged))

    status = {
        'version': __version__,
        'time': time.strftime('%Y-%m-%d %H:%M:%S'),
        'elapsed_sec': round(time.time() - t0, 1),
        'platform_rooms': counts,
        'merge': stats,
        'total': len(merged),
        'sources_file': args.sources,
    }
    if args.dry_run:
        log().info('[dry-run] 将写入 %d 条到 %s', len(merged), MERGED_PATH)
        for _p, _r, ext, url in merged[:8]:
            log().info('  %s', ext)
            log().info('    %s', url[:100])
        return 0

    write_m3u(MERGED_PATH, merged, counts)
    # 每个平台独立的 m3u 文件（项目根目录，供导入/排查）
    for name in sources:
        plat_entries = [(p, r, e, u) for p, r, e, u in merged if p == name]
        write_m3u(os.path.join(ROOT, f'{name}_live.m3u'),
                  plat_entries, {name: len(plat_entries)})
    write_status(os.path.join(OUT_DIR, 'status.json'), status)
    log().info('完成: 已写入 %s 与各平台 *_live.m3u（共 %d 条，耗时 %.1fs）',
               MERGED_PATH, len(merged), status['elapsed_sec'])
    return 0 if merged else 1


if __name__ == '__main__':
    raise SystemExit(main())
