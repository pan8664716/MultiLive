// Package registry 平台注册表：集中注册所有平台（对标 Python 版自动发现）。
// 新增平台：在 internal/platforms/<name>/ 实现 Platform 接口后，在此处加一行即可。
package registry

import (
	"sort"

	"multilive/internal/core"
	"multilive/internal/platform"
	"multilive/internal/platforms/bilibili"
	"multilive/internal/platforms/douyin"
	"multilive/internal/platforms/douyu"
	"multilive/internal/platforms/fourgtv"
	"multilive/internal/platforms/huya"
	"multilive/internal/platforms/kuaishou"
	"multilive/internal/platforms/migu"
	"multilive/internal/platforms/tiktok"
	"multilive/internal/platforms/twitch"
	"multilive/internal/platforms/yy"
)

// All 返回全部平台实例（按名称排序，保证确定性）。
func All() []platform.Platform {
	ps := []platform.Platform{
		fourgtv.Platform{},
		bilibili.Platform{},
		douyin.Platform{},
		douyu.Platform{},
		huya.Platform{},
		kuaishou.Platform{},
		migu.Platform{},
		tiktok.Platform{},
		twitch.Platform{},
		yy.Platform{},
	}
	sort.Slice(ps, func(i, j int) bool { return ps[i].Name() < ps[j].Name() })
	return ps
}

// ByName 返回 {平台名: 实例} 映射。
func ByName() map[string]platform.Platform {
	m := map[string]platform.Platform{}
	for _, p := range All() {
		m[p.Name()] = p
	}
	return m
}

// Names 返回排序后的平台名列表。
func Names() []string {
	ps := All()
	out := make([]string, 0, len(ps))
	for _, p := range ps {
		out = append(out, p.Name())
	}
	return out
}

var _ = core.Version
