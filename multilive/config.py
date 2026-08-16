"""来源配置解析 + 平台注册表。

sources.txt 每行一个来源，支持两种写法：
  1. 显式平台前缀：  douyin:https://live.douyin.com/categorynew/4_105
                     kuaishou:HOT:50
  2. 裸地址：        由各平台自带 parser 依次尝试识别（有歧义时用显式前缀）

新增平台：在 multilive/platforms/ 下新建模块，实现 NAME / parse / fetch
三个约定即可被自动发现（见 platforms/_template.py）。
"""
import importlib
import os
import pkgutil
from dataclasses import dataclass

import multilive.platforms as _plat_pkg
from multilive.core import log


@dataclass
class Source:
    """一个来源（一行配置解析后的结果），语义由平台自行定义。
    meta 为可选的平台私有配置（如快手翻页数）。"""
    platform: str
    kind: str
    target: str
    meta: int = 0


def discover_platforms():
    """自动发现 platforms/ 下所有平台模块（排除以下划线开头的模板）。"""
    found = {}
    for mod in pkgutil.iter_modules(_plat_pkg.__path__):
        if mod.name.startswith('_'):
            continue
        try:
            m = importlib.import_module(f'multilive.platforms.{mod.name}')
        except Exception as e:
            log().warning('平台模块 %s 加载失败: %s', mod.name, e)
            continue
        name = getattr(m, 'NAME', mod.name)
        if callable(getattr(m, 'parse', None)) and callable(getattr(m, 'fetch', None)):
            found[name] = m
        else:
            log().warning('平台模块 %s 缺少 parse/fetch 约定，已跳过', mod.name)
    return found


def load_sources(path, only_platforms=None, require_all=True):
    """读取 sources.txt，返回 {platform_name: [Source, ...]}（保序）。

    only_platforms：CLI --platform 过滤；require_all=False 时某平台解析
    失败只告警（用于只跑单个平台调试）。
    """
    all_platforms = discover_platforms()
    if not all_platforms:
        raise SystemExit('未发现任何平台模块（multilive/platforms/ 为空？）')
    platforms = dict(all_platforms)
    if only_platforms:
        miss = set(only_platforms) - set(platforms)
        if miss:
            raise SystemExit(f'未知平台: {", ".join(sorted(miss))}（可用: {", ".join(sorted(platforms))}）')
        platforms = {k: v for k, v in platforms.items() if k in only_platforms}

    if not os.path.exists(path):
        raise SystemExit(f'缺少来源配置文件 {path}')

    out = {name: [] for name in platforms}
    errors = []
    for lineno, raw in enumerate(open(path, encoding='utf-8'), 1):
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        head = line.split(':', 1)[0] if ':' in line else ''
        if head in all_platforms:
            # 显式前缀：platform:xxx
            if head not in platforms:
                continue  # 该平台被 --platform 过滤，静默跳过
            rest = line.split(':', 1)[1].strip()
            if not rest:
                errors.append(f'第{lineno}行 [{head}] 缺少来源内容')
                continue
            try:
                srcs = platforms[head].parse(rest)
                out[head].extend(srcs or [])
            except Exception as e:
                errors.append(f'第{lineno}行 [{head}] 解析失败: {e}')
            continue
        # 裸地址：按注册顺序（平台名字母序）逐个尝试
        claimed = False
        for name in sorted(platforms):
            try:
                srcs = platforms[name].parse(line)
            except Exception:
                continue
            if srcs:
                out[name].extend(srcs)
                claimed = True
                break
        if not claimed:
            errors.append(f'第{lineno}行无法识别的来源: {line}')

    total = sum(len(v) for v in out.values())
    if errors and total == 0:
        raise SystemExit('\n'.join(errors) if require_all else
                         f'没有解析到任何来源:\n' + '\n'.join(errors))
    for err in errors:
        log().warning('%s', err)
    return {k: v for k, v in out.items() if v}
