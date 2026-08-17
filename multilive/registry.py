"""平台注册表：自动发现 multilive/platforms/ 下所有平台包。

统一约定：每个平台是一个文件夹（文件夹名即平台名），文件夹内的
__init__.py 导出一个 Platform 子类的实例 `platform`。注册表按此
约定自动发现并返回 {平台名: platform 实例}。
"""
import importlib
import pkgutil

import multilive.platforms as _plat_pkg
from multilive.core import log
from multilive.platforms.base import Platform


def discover_platforms():
    """扫描 platforms/ 下所有包，返回 {平台名: Platform 实例}（保序）。"""
    found = {}
    for mod in pkgutil.iter_modules(_plat_pkg.__path__):
        if mod.name.startswith('_') or not mod.ispkg:
            continue   # 只注册「文件夹包」：下划线开头 = 模板/内部，单文件 = 公共库
        try:
            m = importlib.import_module(f'{_plat_pkg.__name__}.{mod.name}')
        except Exception as e:
            log().warning('平台包 %s 加载失败: %s', mod.name, e)
            continue
        plat = getattr(m, 'platform', None)
        if not isinstance(plat, Platform) or not plat.name:
            log().warning('平台包 %s 未导出 Platform 实例（缺 platform 变量），已跳过',
                          mod.name)
            continue
        if callable(getattr(plat, 'parse', None)) \
                and callable(getattr(plat, 'fetch', None)):
            found[plat.name] = plat
        else:
            log().warning('平台 %s 缺少 parse/fetch 约定，已跳过', plat.name)
    return found
