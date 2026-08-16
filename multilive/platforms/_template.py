"""新平台模板（以下划线开头，不会被自动加载）。

一个平台 = 一个模块，只需实现 3 个约定：

```python
NAME = 'example'
keep_stale = False          # 可选：历史房间是否保留（直播直链建议 False）

def parse(line):            # 认领一行 sources.txt 配置
    \"\"\"返回 [Source(platform='example', kind='room', target='123')]；
    本平台不认领该行时返回 []（或抛 ValueError）。\"\"\"
    ...

def fetch(sources, ctx):    # 抓取一个/多个来源
    \"\"\"返回 [Room(platform='example', rid, title, nickname, url, group, avatar)]。
    ctx 提供：ctx.project_root（项目根）、ctx.pages_cap（--pages 上限）\"\"\"
    rooms = []
    for s in sources:
        ...
        rooms.append(Room(platform=NAME, rid=..., title=..., url=..., group=...))
    return rooms
```

约定细节：
  - parse 返回 [] 表示「不认识这行」；抛 ValueError 表示「认领但格式错」。
  - fetch 失败不应整体崩溃：单来源失败打印日志后跳过其余来源。
  - 播放地址拿不到的房间直接跳过（不写入 m3u）。
  - 纯标准库 HTTP 用 multilive.core.Session；GET 一次性的用 core.http_json。
  - 并发抓取用 concurrent.futures.ThreadPoolExecutor，每个线程独立 Session。
"""
