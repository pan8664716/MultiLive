// Package m3u 读写与增量合并（平台无关）。
package m3u

import (
	"encoding/json"
	"fmt"
	"os"
	"regexp"
	"strings"
	"time"

	"multilive/internal/core"
)

// Entry m3u 文件中的一条记录。
type Entry struct {
	Platform string
	RID      string
	ExtInf   string
	URL      string
}

// Stats 增量合并统计。
type Stats struct {
	Added         int `json:"added"`
	Refreshed     int `json:"refreshed"`
	Deduped       int `json:"deduped"`
	KeptStale     int `json:"kept_stale"`
	DroppedStale  int `json:"dropped_stale"`
	DroppedCap    int `json:"dropped_cap"`
}

// MaxPerPlatform 单平台条目上限：超出时从尾部丢弃。
// 合并顺序是「本轮新条目置顶在前、历史在后」，所以砍尾等价于
// 优先丢弃最旧的历史条目（LRU），新鲜在播的不受影响。
const MaxPerPlatform = 10000

func cleanTitle(s string) string {
	s = strings.ReplaceAll(s, "\"", "")
	s = strings.ReplaceAll(s, "\r", "")
	s = strings.ReplaceAll(s, "\n", "")
	s = strings.ReplaceAll(s, ",", "，")
	return strings.TrimSpace(s)
}

// RenderEntry Room -> (EXTINF 行, URL 行)。
func RenderEntry(r core.Room) (string, string) {
	nick := cleanTitle(r.Nickname)
	title := cleanTitle(r.Title)
	var name string
	switch {
	case nick != "" && title != "":
		name = nick + "-" + title
	case nick != "":
		name = nick
	case title != "":
		name = title
	default:
		name = r.RID
	}
	logo := ""
	if strings.HasPrefix(r.Avatar, "http") {
		logo = r.Avatar
	}
	group := cleanTitle(r.Group)
	if group == "" {
		group = r.Platform
	}
	extinf := fmt.Sprintf("#EXTINF:-1 tvg-logo=\"%s\" group-title=\"%s\" tvg-id=\"%s:%s\", %s",
		logo, group, r.Platform, r.RID, name)
	return extinf, r.URL
}

var (
	tvgIDRe = regexp.MustCompile(`tvg-id="([^"]+)"`)
	numRe   = regexp.MustCompile(`/(\d{6,15})`)
)

// ReadExisting 解析已有 m3u，返回条目列表。
func ReadExisting(path string) []Entry {
	var out []Entry
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil
	}
	lines := strings.Split(string(raw), "\n")
	for i := 0; i < len(lines); {
		l := lines[i]
		if strings.HasPrefix(l, "#EXTINF") && i+1 < len(lines) {
			url := strings.TrimSpace(lines[i+1])
			if strings.HasPrefix(url, "http") {
				if m := tvgIDRe.FindStringSubmatch(l); m != nil {
					tid := m[1]
					if p, r, ok := strings.Cut(tid, ":"); ok {
						out = append(out, Entry{Platform: p, RID: r, ExtInf: l, URL: url})
					} else {
						out = append(out, Entry{Platform: "douyin", RID: tid, ExtInf: l, URL: url})
					}
				} else if m := numRe.FindStringSubmatch(url); m != nil {
					out = append(out, Entry{Platform: "douyin", RID: m[1], ExtInf: l, URL: url})
				}
			}
			i += 2
			continue
		}
		i++
	}
	return out
}

type pair struct{ p, r string }

// Merge 增量合并：去重 -> 本轮置顶 -> 历史按 keepStale 保留/丢弃。
func Merge(existing []Entry, rooms []core.Room, keepStale map[string]bool, fallback func(core.Room) string) ([]Entry, Stats) {
	var st Stats
	seenOld := map[pair]bool{}
	var uniq []Entry
	for _, e := range existing {
		k := pair{e.Platform, e.RID}
		if seenOld[k] {
			continue
		}
		seenOld[k] = true
		uniq = append(uniq, e)
	}
	st.Deduped = len(existing) - len(uniq)
	oldKeys := map[pair]bool{}
	for _, e := range uniq {
		oldKeys[pair{e.Platform, e.RID}] = true
	}

	var out []Entry
	seenNew := map[pair]bool{}
	for _, r := range rooms {
		k := pair{r.Platform, r.RID}
		if seenNew[k] {
			continue
		}
		seenNew[k] = true
		url := r.URL
		if url == "" && fallback != nil {
			url = fallback(r)
		}
		if url == "" {
			continue
		}
		rc := r
		rc.URL = url
		extinf, _ := RenderEntry(rc)
		out = append(out, Entry{Platform: r.Platform, RID: r.RID, ExtInf: extinf, URL: url})
		if oldKeys[k] {
			st.Refreshed++
		} else {
			st.Added++
		}
	}
	for _, e := range uniq {
		if seenNew[pair{e.Platform, e.RID}] {
			continue
		}
		ks, ran := keepStale[e.Platform]
		if !ran || ks {
			out = append(out, e)
			st.KeptStale++
		} else {
			st.DroppedStale++
		}
	}
	// 单平台条目上限：合并顺序是「本轮在前、历史在后」，从尾部丢弃
	// 等价于优先丢最旧的历史条目，新鲜在播的不受影响。
	counts := map[string]int{}
	for _, e := range out {
		counts[e.Platform]++
	}
	over := map[string]int{}
	for p, n := range counts {
		if n > MaxPerPlatform {
			over[p] = n - MaxPerPlatform
		}
	}
	if len(over) > 0 {
		// 注意：必须新分配切片，不能复用 out 的底层数组（边读边写会错乱）。
		kept := make([]Entry, 0, len(out))
		// 从尾往前数每个平台要丢的个数，保证丢的是最旧的历史条目。
		drop := map[string]int{}
		for p, n := range over {
			drop[p] = n
		}
		for i := len(out) - 1; i >= 0; i-- {
			if drop[out[i].Platform] > 0 {
				drop[out[i].Platform]--
				st.DroppedCap++
				st.KeptStale--
				continue
			}
			kept = append(kept, out[i])
		}
		// 倒序恢复原顺序
		for i, j := 0, len(kept)-1; i < j; i, j = i+1, j-1 {
			kept[i], kept[j] = kept[j], kept[i]
		}
		out = kept
	}
	return out, st
}

// WriteM3U 原子写 m3u 文件。
func WriteM3U(path string, entries []Entry, counts map[string]int) error {
	var b strings.Builder
	b.WriteString("#EXTM3U\n")
	fmt.Fprintf(&b, "# 生成时间: %s\n", time.Now().Format("2006-01-02 15:04:05"))
	fmt.Fprintf(&b, "# 房间数: %d\n", len(entries))
	parts := make([]string, 0, len(counts))
	for _, k := range sortedKeys(counts) {
		parts = append(parts, fmt.Sprintf("%s=%d", k, counts[k]))
	}
	b.WriteString("# 各平台: " + strings.Join(parts, ", ") + "\n")
	for _, e := range entries {
		b.WriteString(e.ExtInf + "\n" + e.URL + "\n")
	}
	return atomicWrite(path, []byte(b.String()))
}

// WriteStatus 写机器可读运行摘要。
func WriteStatus(path string, data any) error {
	raw, err := json.MarshalIndent(data, "", "  ")
	if err != nil {
		return err
	}
	if err := os.MkdirAll(dirOf(path), 0o755); err != nil {
		return err
	}
	return atomicWrite(path, append(raw, '\n'))
}

func atomicWrite(path string, data []byte) error {
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, data, 0o644); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}

func sortedKeys(m map[string]int) []string {
	ks := make([]string, 0, len(m))
	for k := range m {
		ks = append(ks, k)
	}
	for i := 1; i < len(ks); i++ {
		for j := i; j > 0 && ks[j] < ks[j-1]; j-- {
			ks[j], ks[j-1] = ks[j-1], ks[j]
		}
	}
	return ks
}

func dirOf(p string) string {
	if i := strings.LastIndex(p, "/"); i >= 0 {
		return p[:i]
	}
	return "."
}
