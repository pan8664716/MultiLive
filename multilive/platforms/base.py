"""平台基类与公共小工具（统一契约）。

统一约定：新增平台 = 在 multilive/platforms/ 下新建一个文件夹
（文件夹名即平台名），在 __init__.py 里定义 Platform 子类并导出
一个 platform 实例，注册表自动发现（模板见 platforms/_template.py）。

基类提供的公共能力：
  - name / keep_stale / max_workers：统一契约字段
  - parse / fetch：必须实现的两个约定方法
  - fallback_url：可选钩子（keep_stale=True 时给历史房间兜底播放地址）
  - parallel_map：并发执行 + 保序返回，替代手写 ThreadPoolExecutor
"""
from concurrent.futures import ThreadPoolExecutor

from multilive.core import log


class Platform:
    """平台基类：所有平台通过继承本类实现统一契约。"""

    name = ''               # 平台名：sources.txt 前缀 / 日志 / tvg-id
    keep_stale = False      # 本轮未抓到（下播）的历史条目是否保留
    max_workers = 3         # parallel_map 默认并发数（子类可调）

    def parse(self, line):
        """认领一行 sources.txt，返回 [Source]；不认识返回 []。"""
        raise NotImplementedError(f'{self.name}.parse 未实现')

    def fetch(self, sources, ctx):
        """抓取 sources，返回 [Room]；单来源失败打印日志后继续。"""
        raise NotImplementedError(f'{self.name}.fetch 未实现')

    def fallback_url(self, room):
        """可选：keep_stale=True 时，为历史房间补兜底播放地址。"""
        return None

    def log(self):
        """平台内可直接用 self.log() 打日志（模块名作前缀，方便排查）。"""
        return log()

    def parallel_map(self, fn, items, workers=None):
        """并发执行 fn(item)，返回按输入顺序的结果列表。

        每个任务的 Session 由 fn 自行创建（独立 CookieJar 避免线程竞争）。
        """
        items = list(items)
        if not items:
            return []
        with ThreadPoolExecutor(
                max_workers=workers or min(self.max_workers, len(items))) as ex:
            return list(ex.map(fn, items))


def best_url_by_quality(mapping, order):
    """按清晰度优先级取第一个可用 URL（CDN 直链配额用）。"""
    for q in order:
        u = (mapping or {}).get(q) or ''
        if u:
            return u
    return ''


def first_valid_url(*vals):
    """返回第一个非空字符串（多字段取值时的惯用写法）。"""
    for v in vals:
        if isinstance(v, str) and v:
            return v
    return ''
