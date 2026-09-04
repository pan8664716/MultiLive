// Package platform 定义平台统一契约：每个平台实现 Platform 接口即可被自动注册。
package platform

import "multilive/internal/core"

// Platform 平台契约（对标 Python 版 Platform 基类）。
type Platform interface {
	// Name 平台名：sources.txt 前缀 / 日志 / tvg-id。
	Name() string
	// KeepStale 本轮未抓到（下播）的历史条目是否保留。
	KeepStale() bool
	// Parse 认领一行 sources.txt；不认识返回 (nil, nil)；格式错返回 error。
	Parse(line string) ([]core.Source, error)
	// Fetch 抓取并返回统一 Room；单来源失败只记日志，不整体崩溃。
	Fetch(sources []core.Source, ctx *core.Ctx) ([]core.Room, error)
	// FallbackURL 历史条目兜底播放地址（keepStale 平台可选）。
	FallbackURL(r core.Room) string
}

// Base 提供 FallbackURL 默认实现，平台可嵌入。
type Base struct{}

func (Base) FallbackURL(core.Room) string { return "" }
