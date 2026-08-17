"""核心基础：Room 数据模型、HTTP 会话、日志工具。

所有平台只依赖本文件提供的工具（Session / log / Room / Source），
不直接读取文件或命令行参数，保证「加新平台 = 只写一个平台文件夹」。
"""
import http.cookiejar
import json
import logging
import time
import urllib.request
from dataclasses import dataclass, field

UA_CHROME = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
             '(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36')


@dataclass
class Room:
    """平台无关的直播间数据，最终渲染成 m3u 条目。"""
    platform: str          # 平台名，如 'douyin'
    rid: str               # 平台内唯一房间号
    title: str = ''
    nickname: str = ''
    url: str = ''          # 播放地址（CDN 直链 / 解析地址）
    group: str = ''        # m3u group-title 分类名
    avatar: str = ''
    extra: dict = field(default_factory=dict)  # 平台私有字段，随条目透传


@dataclass
class Source:
    """一个来源（一行配置解析后的结果），语义由平台自行定义。
    meta 为可选的平台私有配置（如快手翻页数）。"""
    platform: str
    kind: str
    target: str
    meta: int = 0


def log():
    """返回平台模块可用的 logger（模块名作为日志前缀，方便排查）。"""
    return logging.getLogger('multilive')


class Session:
    """带 CookieJar 的 HTTP 会话（urllib 实现，无第三方依赖）。

    每个线程单独持有一个 Session 实例，避免共享 CookieJar 的竞争。
    """

    def __init__(self, headers=None):
        self.cj = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cj))
        self.base_headers = {'User-Agent': UA_CHROME,
                             'Accept-Language': 'zh-CN,zh;q=0.9'}
        if headers:
            self.base_headers.update(headers)

    def request(self, url, data=None, referer=None, accept=None,
                timeout=30, headers=None):
        hdr = dict(self.base_headers)
        if accept:
            hdr['Accept'] = accept
        if referer:
            hdr['Referer'] = referer
        if headers:
            hdr.update(headers)
        if isinstance(data, str):
            body = data.encode()          # 调用方已拼好 body（如表单编码）
        elif data is not None:
            body = json.dumps(data).encode()
        else:
            body = None
        if body is not None and 'Content-Type' not in hdr:
            hdr['Content-Type'] = 'application/json'
        req = urllib.request.Request(url, data=body, headers=hdr)
        r = self.op.open(req, timeout=timeout)
        return r.status, r.read(), dict(r.headers)

    def get(self, url, **kw):
        return self.request(url, **kw)

    def get_text(self, url, **kw):
        st, raw, hdrs = self.request(url, **kw)
        return st, raw.decode('utf-8', 'ignore'), hdrs

    def get_json(self, url, **kw):
        st, raw, hdrs = self.request(url, **kw)
        try:
            return st, json.loads(raw), hdrs
        except ValueError:
            raise RuntimeError(f'非JSON响应(status={st}): {raw[:120]!r}')

    def post_json(self, url, data, **kw):
        st, raw, hdrs = self.request(url, data=data, **kw)
        try:
            return st, json.loads(raw), hdrs
        except ValueError:
            raise RuntimeError(f'非JSON响应(status={st}): {raw[:120]!r}')


def timed(fn):
    """打印耗时的装饰器（平台级统计用）。"""
    def wrapper(*a, **kw):
        t0 = time.time()
        out = fn(*a, **kw)
        log().info('耗时 %.1fs', time.time() - t0)
        return out
    return wrapper


def fmt_exc(e, limit=120):
    text = str(e).strip()
    return (text[:limit] + '…') if len(text) > limit else (text or type(e).__name__)
