"""4gtv 台湾直播（频道列表内置静态清单；播放地址走 Worker 动态解析）。

4gtv 官方接口（api2.4gtv.tv/App/GetChannelUrl2）需要台湾 IP 且逐频道
签名（4gtv_auth + fsenc_key），CI/数据中心无法直连、也不符合「不逐房间
取流」铁律；因此列表采用内置静态清单 channels.tsv（频道总数稳定，约 56
个），m3u 统一写 https://astar.cc.cd/4gtv/<频道ID> —— Worker 点播时
实时解析（参考 4gtv 解析脚本：GetChannelUrl2 + 4gtv_auth 生成 +
1080p 级联回退，需台湾出口）。

m3u 语义：keep_stale=True——IPTV 频道稳定，保留历史条目。
"""
import os

from multilive.core import Room, Source, log
from multilive.platforms.base import Platform

NAME = '4gtv'
keep_stale = True

PLAYER_BASE = 'https://astar.cc.cd/4gtv/{}'
CHANNELS_FILE = 'channels.tsv'

def load_channels():
    """读内置清单 -> [(id, name, group)]。"""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        CHANNELS_FILE)
    rows = []
    for line in open(path, encoding='utf-8'):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        parts = line.split('\t')
        if len(parts) < 2:
            continue
        cid, name = parts[0].strip(), parts[1].strip()
        group = parts[2].strip() if len(parts) > 2 else ''
        if cid:
            rows.append((cid, name, group))
    return rows

class FourGTVPlatform(Platform):
    """4gtv 平台：内置频道清单；播放地址走 Worker 动态解析。"""
    name = NAME
    keep_stale = keep_stale
    max_workers = 1

    def parse(self, line):
        t = line.strip().upper()
        if t != 'ALL':
            return []
        return [Source(self.name, 'list', 'ALL')]

    def fetch(self, sources, ctx):
        log().info('[4gtv] %d 个来源', len(sources))
        if not any(s.kind == 'list' for s in sources):
            return []
        out = []
        for cid, name, group in load_channels():
            out.append(Room(
                platform=self.name, rid=cid, title=name, nickname='',
                url=PLAYER_BASE.format(cid),
                group=group or self.name))
        log().info('[4gtv] 完成: %d 频道（内置清单，地址走 Worker 动态解析）',
                   len(out))
        return out

platform = FourGTVPlatform()
