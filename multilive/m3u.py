"""m3u 读写与增量合并（平台无关）。

合并规则（沿用 douyin 方案的增量语义，全局统一）：
  - 本轮抓到的房间按平台/来源顺序置顶；
  - 旧列表中与「本轮抓到」重复的条目删除（全局去重，key=平台+房间号）；
  - 本轮未抓到的历史条目：
      keep_stale=True  的平台（如 douyin，有兜底解析地址）按原顺序保留；
      keep_stale=False 的平台（如 kuaishou，直链下播即失效）直接丢弃。
"""
import json
import os
import re
import time

from multilive.core import Room, log


def clean_title(s):
    return re.sub(r'["\r\n]', '', s or '').replace(',', '，').strip()


def render_entry(room: Room):
    """Room -> (EXTINF 行, URL 行)。平台私有字段可用 extra 覆盖。"""
    nick = clean_title(room.nickname)
    title = clean_title(room.title)
    if nick and title:
        name = f'{nick}-{title}'
    elif nick:
        name = nick
    elif title:
        name = title
    else:
        name = room.rid
    logo = room.avatar if room.avatar.startswith('http') else ''
    group = clean_title(room.group) or room.platform
    url = room.url or room.extra.get('fallback_url', '')
    extinf = (f'#EXTINF:-1 tvg-logo="{logo}" group-title="{group}" '
              f'tvg-id="{room.platform}:{room.rid}", {name}')
    return extinf, url


def read_existing(path):
    """解析已有 m3u：返回 (平台头行, [(platform, rid, extinf, url)])。
    按 tvg-id「platform:rid」识别；兼容老格式纯数字 tvg-id（视为 douyin）。
    """
    entries = []
    if not os.path.exists(path):
        return entries
    lines = open(path, encoding='utf-8').read().splitlines()
    i = 0
    while i < len(lines):
        l = lines[i]
        if l.startswith('#EXTINF') and i + 1 < len(lines):
            url = lines[i + 1]
            if url.startswith('http'):
                m = re.search(r'tvg-id="([^"]+)"', l)
                rid = None
                if m:
                    tid = m.group(1)
                    plat, sep, rid = tid.partition(':')
                    if not sep:
                        plat, rid = 'douyin', tid   # 老格式数字 id 视为 douyin
                else:
                    mm = re.search(r'/(\d{6,15})', url)
                    if mm:
                        plat, rid = 'douyin', mm.group(1)
                if rid:
                    entries.append((plat, rid, l, url))
            i += 2
            continue
        i += 1
    return entries


def merge(existing, new_rooms, keep_stale, fallback_fn=None):
    """增量合并。
    existing:      read_existing 的结果
    new_rooms:     本轮 Room 列表（已按平台/来源顺序）
    fallback_fn:   (room) -> url|None，为置顶条目补兜底地址（douyin 用）
    返回 (新条目列表, 统计dict)
    """
    new_entries = []
    seen_new = set()
    old_keys = {(p, r) for p, r, _, _ in existing}
    stats = {'added': 0, 'refreshed': 0, 'dropped_stale': 0, 'kept_stale': 0}

    # 本轮抓到的房间置顶（全局去重）
    for r in new_rooms:
        key = (r.platform, r.rid)
        if key in seen_new:
            continue
        seen_new.add(key)
        url = r.url or (fallback_fn(r) if fallback_fn else '')
        if not url:
            continue
        extinf, _ = render_entry(r)
        new_entries.append((r.platform, r.rid, extinf, url))
        if key in old_keys:
            stats['refreshed'] += 1
        else:
            stats['added'] += 1

    # 历史条目：本轮已抓到则让位；未抓到按平台策略保留/丢弃
    for plat, rid, extinf, url in existing:
        if (plat, rid) in seen_new:
            continue
        if keep_stale.get(plat, False):
            new_entries.append((plat, rid, extinf, url))
            stats['kept_stale'] += 1
        else:
            stats['dropped_stale'] += 1

    return new_entries, stats


def write_m3u(path, entries, platform_counts):
    header = [
        '#EXTM3U',
        f'# 生成时间: {time.strftime("%Y-%m-%d %H:%M:%S")}',
        f'# 房间数: {len(entries)}',
        f'# 各平台: ' + ', '.join(f'{k}={v}' for k, v in platform_counts.items()),
    ]
    lines = header + [ln for _, _, extinf, url in entries for ln in (extinf, url)]
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    os.replace(tmp, path)


def write_status(path, data):
    """输出每次运行的机器可读摘要（配合日志排查定时任务问题）。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
