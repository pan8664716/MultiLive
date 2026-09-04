// Package core 提供 Room/Source 统一模型、Ctx 上下文与 JSON 小工具。
package core

const Version = "3.0.0"

const UAChrome = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
	"(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"

// Room 平台无关的直播间数据，最终渲染成 m3u 条目。
type Room struct {
	Platform string // 平台名，如 douyin
	RID      string // 平台内唯一房间号
	Title    string
	Nickname string
	URL      string // 播放地址（CDN 直链 / Worker 解析地址）
	Group    string // m3u group-title 分类名
	Avatar   string
}

// Source 一行配置解析后的单个来源，语义由平台自行定义。
type Source struct {
	Platform string
	Kind     string
	Target   string
	Meta     int // 平台私有整数配置（如翻页数）
}

// Ctx 抓取上下文：项目根（读 tools/ 辅助脚本）与 --pages 上限。
type Ctx struct {
	ProjectRoot string
	PagesCap    int
}

// JMap 取嵌套 map 字段。
func JMap(m map[string]any, key string) map[string]any {
	if m == nil {
		return nil
	}
	v, _ := m[key].(map[string]any)
	return v
}

// JList 取嵌套 list 字段。
func JList(m map[string]any, key string) []any {
	if m == nil {
		return nil
	}
	v, _ := m[key].([]any)
	return v
}

// JStr 按顺序取第一个非空字符串字段。
func JStr(m map[string]any, keys ...string) string {
	if m == nil {
		return ""
	}
	for _, k := range keys {
		switch v := m[k].(type) {
		case string:
			if v != "" {
				return v
			}
		case float64:
			if v != 0 {
				return itoa(int64(v))
			}
		}
	}
	return ""
}

func itoa(n int64) string {
	if n == 0 {
		return "0"
	}
	neg := n < 0
	if neg {
		n = -n
	}
	var b [20]byte
	i := len(b)
	for n > 0 {
		i--
		b[i] = byte('0' + n%10)
		n /= 10
	}
	if neg {
		i--
		b[i] = '-'
	}
	return string(b[i:])
}

// FirstStr 返回第一个非空字符串。
func FirstStr(vals ...string) string {
	for _, v := range vals {
		if v != "" {
			return v
		}
	}
	return ""
}

// Parallel 并发执行 fn 并保序返回结果（替代手写 WaitGroup）。
func Parallel[T any, R any](workers int, items []T, fn func(T) R) []R {
	if len(items) == 0 {
		return nil
	}
	if workers < 1 {
		workers = 1
	}
	if workers > len(items) {
		workers = len(items)
	}
	out := make([]R, len(items))
	sem := make(chan struct{}, workers)
	done := make(chan int, len(items))
	for i, it := range items {
		sem <- struct{}{}
		go func(i int, it T) {
			out[i] = fn(it)
			<-sem
			done <- i
		}(i, it)
	}
	for range items {
		<-done
	}
	return out
}
