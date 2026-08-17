"""新平台模板（以下划线开头，不会被自动加载）。

统一约定：新增平台 = 新建一个文件夹 multilive/platforms/<平台名>/，
在 __init__.py 里完成下面三件事，注册表（multilive/registry.py）即自动发现：

    1. 定义 Platform 子类（实现 parse / fetch）
    2. 在类里声明 name / keep_stale（可选 max_workers）
    3. 文件末尾导出 platform 实例

实现变大后，可在平台文件夹内继续拆分 api.py / stream.py 等子模块，
__init__.py 只保留契约出口（类 + platform 实例），保持入口稳定。

```python
import re

from multilive.core import Room, Session, Source, log
from multilive.platforms.base import Platform


class ExamplePlatform(Platform):
    name = 'example'            # 平台名：sources.txt 前缀 / 日志 / tvg-id
    keep_stale = False          # True=保留下播历史（需实现 fallback_url 兜底）
    max_workers = 3             # parallel_map 默认并发数（可选）

    def parse(self, line):
        \"\"\"认领一行 sources.txt；不认识返回 []；格式错可抛 ValueError。\"\"\"
        m = re.match(r'https?://example\\.com/(\\d+)', line.strip())
        if m:
            return [Source(self.name, 'room', m.group(1))]
        return []

    def fetch(self, sources, ctx):
        \"\"\"抓取并返回 [Room]；单来源失败打印日志后继续（不整体崩溃）。\"\"\"
        rooms = []
        for s in sources:
            try:
                sess = Session()
                _st, j, _ = sess.get_json('https://example.com/api')
                for it in (j.get('data') or []):
                    rooms.append(Room(platform=self.name,
                                      rid=str(it['id']),
                                      title=it.get('title') or '',
                                      nickname=it.get('nick') or '',
                                      url=it.get('url') or '',
                                      group=it.get('game') or self.name))
            except Exception as e:
                log().warning('[example] 来源 %s 失败: %s', s.target, e)
        return rooms

    def fallback_url(self, room):
        \"\"\"可选：keep_stale=True 时，为历史房间补兜底播放地址。\"\"\"
        return f'https://example.m3u8.dev/{room.rid}'


platform = ExamplePlatform()
```

约定细节：
  - parse 返回 [] 表示「不认识这行」；抛 ValueError 表示「认领但格式错」。
  - fetch 失败不应整体崩溃：单来源失败打印日志后跳过其余来源。
  - 播放地址拿不到的房间直接跳过（不写入 m3u）。
  - 纯标准库 HTTP 用 multilive.core.Session（get/get_text/get_json/post_json）。
  - 并发抓取优先用 self.parallel_map(fn, items)，每个任务内独立 Session。
  - ctx 提供：ctx.project_root（项目根）、ctx.pages_cap（--pages 上限）。
"""
