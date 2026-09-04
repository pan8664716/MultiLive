// Package config 来源配置解析（sources.txt）。
package config

import (
	"fmt"
	"os"
	"strings"

	"multilive/internal/core"
	"multilive/internal/platform"
)

// LoadSources 读取 sources.txt，返回 {平台名: [Source]} 与平台顺序（保序）。
// only 非空时只保留指定平台；requireAll=false 时单平台调试允许部分解析失败。
func LoadSources(path string, plats map[string]platform.Platform, order []string, only []string, requireAll bool) (map[string][]core.Source, []string, error) {
	keep := map[string]bool{}
	if len(only) > 0 {
		for _, n := range only {
			if _, ok := plats[n]; !ok {
				return nil, nil, fmt.Errorf("未知平台: %s（可用: %s）", n, strings.Join(order, ", "))
			}
			keep[n] = true
		}
	} else {
		for _, n := range order {
			keep[n] = true
		}
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, nil, fmt.Errorf("缺少来源配置文件 %s", path)
	}
	out := map[string][]core.Source{}
	for _, n := range order {
		if keep[n] {
			out[n] = nil
		}
	}
	var errors []string
	for lineno, line := range strings.Split(string(raw), "\n") {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		head := ""
		if i := strings.Index(line, ":"); i >= 0 {
			head = line[:i]
		}
		if _, ok := plats[head]; ok {
			if !keep[head] {
				continue // 被 --platform 过滤，静默跳过
			}
			rest := strings.TrimSpace(line[len(head)+1:])
			if rest == "" {
				errors = append(errors, fmt.Sprintf("第%d行 [%s] 缺少来源内容", lineno+1, head))
				continue
			}
			srcs, err := plats[head].Parse(rest)
			if err != nil {
				errors = append(errors, fmt.Sprintf("第%d行 [%s] 解析失败: %s", lineno+1, head, err))
				continue
			}
			out[head] = append(out[head], srcs...)
			continue
		}
		// 裸地址：按名称排序逐个尝试认领
		claimed := false
		for _, name := range order {
			if !keep[name] {
				continue
			}
			srcs, err := plats[name].Parse(line)
			if err != nil || len(srcs) == 0 {
				continue
			}
			out[name] = append(out[name], srcs...)
			claimed = true
			break
		}
		if !claimed {
			errors = append(errors, fmt.Sprintf("第%d行无法识别的来源: %s", lineno+1, line))
		}
	}
	total := 0
	for _, v := range out {
		total += len(v)
	}
	if len(errors) > 0 && total == 0 && requireAll {
		return nil, nil, fmt.Errorf("%s", strings.Join(errors, "\n"))
	}
	if len(errors) > 0 && total == 0 {
		return nil, nil, fmt.Errorf("没有解析到任何来源:\n%s", strings.Join(errors, "\n"))
	}
	for _, e := range errors {
		core.Warnf("%s", e)
	}
	// 去掉空平台，保持顺序
	var names []string
	for _, n := range order {
		if len(out[n]) > 0 {
			names = append(names, n)
		} else {
			delete(out, n)
		}
	}
	return out, names, nil
}
