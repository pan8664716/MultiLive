// MultiLive Go 版统一入口：多平台直播 m3u 聚合更新。
//
// 用法：
//
//	multilive                        # 按 sources.txt 抓取并写 output/multilive.m3u
//	multilive --dry-run              # 只打印统计，不写文件
//	multilive --platform douyin      # 只跑指定平台（逗号分隔）
//	multilive --pages 10             # 限制单来源翻页数
//	multilive --verbose              # 控制台输出 DEBUG 日志
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"

	"multilive/internal/config"
	"multilive/internal/core"
	"multilive/internal/m3u"
	"multilive/internal/registry"
)

// Disabled 暂时下线的平台：不抓取、输出清空（每轮自动把历史条目剔除）。
var Disabled = map[string]bool{}

func main() {
	os.Exit(run())
}

func run() int {
	var dryRun bool
	var platformFlag string
	var pages int
	var sourcesPath string
	var verbose bool
	var showVersion bool
	flag.BoolVar(&dryRun, "dry-run", false, "只打印统计，不写文件")
	flag.StringVar(&platformFlag, "platform", "", "只跑指定平台，逗号分隔")
	flag.IntVar(&pages, "pages", 0, "限制单来源翻页数上限（默认跟随各平台配置）")
	flag.StringVar(&sourcesPath, "sources", "", "来源配置文件路径")
	flag.BoolVar(&verbose, "verbose", false, "控制台输出 DEBUG")
	flag.BoolVar(&showVersion, "version", false, "打印版本号")
	flag.Parse()

	root, err := projectRoot()
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		return 1
	}
	outDir := filepath.Join(root, "output")
	mergedPath := filepath.Join(outDir, "multilive.m3u")
	logPath := filepath.Join(outDir, "run.log")
	if sourcesPath == "" {
		sourcesPath = filepath.Join(root, "sources.txt")
	}
	core.SetupLogging(verbose, logPath)
	if showVersion {
		fmt.Println("MultiLive", core.Version)
		return 0
	}
	core.Infof("MultiLive v%s 启动", core.Version)

	plats := registry.ByName()
	order := registry.Names()
	var only []string
	if strings.TrimSpace(platformFlag) != "" {
		for _, s := range strings.Split(platformFlag, ",") {
			if t := strings.TrimSpace(s); t != "" {
				only = append(only, t)
			}
		}
	}
	sources, names, err := config.LoadSources(sourcesPath, plats, order, only, true)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		core.Errorf("%s", err)
		return 1
	}
	if len(sources) == 0 {
		fmt.Fprintln(os.Stderr, "sources.txt 没有可用的来源，请先配置（见 README）")
		return 1
	}
	if len(Disabled) > 0 {
		var ds []string
		for n := range Disabled {
			ds = append(ds, n)
		}
		sort.Strings(ds)
		for _, n := range ds {
			core.Infof("[%s] 已暂时下线，本轮不抓取、输出清空", n)
		}
		for n := range Disabled {
			delete(sources, n)
		}
		names = names[:0]
		for _, n := range order {
			if _, ok := sources[n]; ok {
				names = append(names, n)
			}
		}
		if len(sources) == 0 {
			fmt.Fprintln(os.Stderr, "所有平台均已下线，无可抓取来源")
			return 1
		}
	}

	ctx := &core.Ctx{ProjectRoot: root, PagesCap: pages}
	t0 := time.Now()
	var mu sync.Mutex
	perPlatform := map[string][]core.Room{}
	var newRooms []core.Room
	var wg sync.WaitGroup
	for _, name := range names {
		wg.Add(1)
		go func(name string) {
			defer wg.Done()
			rooms, err := plats[name].Fetch(sources[name], ctx)
			if err != nil {
				core.Errorf("[%s] 平台抓取失败: %s", name, core.FmtErr(err, 120))
				mu.Lock()
				perPlatform[name] = nil
				mu.Unlock()
				return
			}
			mu.Lock()
			perPlatform[name] = rooms
			newRooms = append(newRooms, rooms...)
			mu.Unlock()
		}(name)
	}
	wg.Wait()

	keepStale := map[string]bool{}
	for name := range sources {
		keepStale[name] = plats[name].KeepStale()
	}
	var fallback func(core.Room) string
	if d, ok := plats["douyin"]; ok {
		fallback = d.FallbackURL
	}
	existing := m3u.ReadExisting(mergedPath)
	// 下线平台历史一并剔除
	if len(Disabled) > 0 {
		var kept []m3u.Entry
		for _, e := range existing {
			if !Disabled[e.Platform] {
				kept = append(kept, e)
			}
		}
		existing = kept
	}
	merged, stats := m3u.Merge(existing, newRooms, keepStale, fallback)
	if len(Disabled) > 0 {
		var kept []m3u.Entry
		for _, e := range merged {
			if !Disabled[e.Platform] {
				kept = append(kept, e)
			}
		}
		merged = kept
	}

	counts := map[string]int{}
	for name, rooms := range perPlatform {
		counts[name] = len(rooms)
	}
	core.Infof("抓取统计: %s", formatCounts(counts))
	core.Infof("合并统计: 新增=%d 刷新=%d 去重=%d 保留历史=%d 丢弃失效=%d 合计=%d",
		stats.Added, stats.Refreshed, stats.Deduped, stats.KeptStale, stats.DroppedStale, len(merged))
	status := map[string]any{
		"version":        core.Version,
		"time":           time.Now().Format("2006-01-02 15:04:05"),
		"elapsed_sec":    round1(time.Since(t0).Seconds()),
		"platform_rooms": counts,
		"merge":          stats,
		"total":          len(merged),
		"sources_file":   sourcesPath,
	}
	if dryRun {
		core.Infof("[dry-run] 将写入 %d 条到 %s", len(merged), mergedPath)
		for i, e := range merged {
			if i >= 8 {
				break
			}
			core.Infof("  %s", e.ExtInf)
			core.Infof("    %s", trunc(e.URL, 100))
		}
		return 0
	}
	if err := os.MkdirAll(outDir, 0o755); err != nil {
		core.Errorf("创建 output 目录失败: %s", err)
		return 1
	}
	if err := m3u.WriteM3U(mergedPath, merged, counts); err != nil {
		core.Errorf("写入聚合 m3u 失败: %s", err)
		return 1
	}
	written := map[string]bool{}
	for _, name := range names {
		var part []m3u.Entry
		for _, e := range merged {
			if e.Platform == name {
				part = append(part, e)
			}
		}
		pc := map[string]int{name: len(part)}
		if err := m3u.WriteM3U(filepath.Join(outDir, name+"_live.m3u"), part, pc); err != nil {
			core.Errorf("[%s] 写平台 m3u 失败: %s", name, err)
		}
		written[name] = true
	}
	for n := range Disabled {
		pc := map[string]int{n: 0}
		if err := m3u.WriteM3U(filepath.Join(outDir, n+"_live.m3u"), nil, pc); err != nil {
			core.Errorf("[%s] 清空下线平台 m3u 失败: %s", n, err)
		}
	}
	if err := m3u.WriteStatus(filepath.Join(outDir, "status.json"), status); err != nil {
		core.Errorf("写入 status.json 失败: %s", err)
		return 1
	}
	raw, _ := json.Marshal(status)
	_ = raw
	core.Infof("完成: 已写入 %s 与各平台 *_live.m3u（共 %d 条，耗时 %.1fs）",
		mergedPath, len(merged), round1(time.Since(t0).Seconds()))
	if len(merged) == 0 {
		return 1
	}
	return 0
}

func projectRoot() (string, error) {
	exe, err := os.Executable()
	if err == nil {
		// 开发态：cwd 即项目根；二进制放在项目内时向上找 sources.txt。
		dir := filepath.Dir(exe)
		for i := 0; i < 4; i++ {
			if _, err := os.Stat(filepath.Join(dir, "sources.txt")); err == nil {
				return dir, nil
			}
			dir = filepath.Dir(dir)
		}
	}
	cwd, err := os.Getwd()
	if err != nil {
		return "", err
	}
	return cwd, nil
}

func formatCounts(counts map[string]int) string {
	ks := make([]string, 0, len(counts))
	for k := range counts {
		ks = append(ks, k)
	}
	sort.Strings(ks)
	parts := make([]string, 0, len(ks))
	for _, k := range ks {
		parts = append(parts, fmt.Sprintf("%s=%d", k, counts[k]))
	}
	return "{" + strings.Join(parts, ", ") + "}"
}

func round1(f float64) float64 {
	return float64(int(f*10+0.5)) / 10
}

func trunc(s string, n int) string {
	r := []rune(s)
	if len(r) > n {
		return string(r[:n])
	}
	return s
}
