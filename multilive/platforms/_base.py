"""平台公共小工具。"""
import re


def best_url_by_quality(mapping, order):
    """按清晰度优先级取第一个可用的 URL（CDN 直链配额用）。"""
    for q in order:
        u = (mapping or {}).get(q) or ''
        if u:
            return u
    return ''


def first_valid_url(*vals):
    for v in vals:
        if isinstance(v, str) and v:
            return v
    return ''


def clean(s):
    return re.sub(r'["\r\n]', '', s or '').replace(',', '，').strip()
