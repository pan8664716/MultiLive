// Package douyin 抖音直播：分类接口 / 房间接口 / 页面内嵌 RSC 三级降级。
// 浏览器兜底通过 tools/browser_fetch_douyin.mjs（Node + patchright，可选）。
package douyin

import (
	"encoding/json"
	"fmt"
	"net/url"
	"os"
	"os/exec"
	"regexp"
	"strconv"
	"strings"
	"time"

	"multilive/internal/core"
	"multilive/internal/platform"
)

const playerBase = "https://astar.cc.cd/douyin/%s"

const (
	maxPages    = 14
	pageSleep   = 1200 * time.Millisecond
	retrySleep  = 2 * time.Second
	categoryTry = 3
	maxWorkers  = 5
	browserTO   = 300 * time.Second
	aBogusParam = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
)

var (
	catRe  = regexp.MustCompile(`^https?://live\.douyin\.com/categorynew/([\d_]+)`)
	roomRe = regexp.MustCompile(`^https?://live\.douyin\.com/(\d+)`)
	numRe  = regexp.MustCompile(`^\d{6,15}$`)
	paceRe = regexp.MustCompile(`self\.__pace_f\.push\(\[1,"`)
	webRID = regexp.MustCompile(`"web_rid":"(\d{6,15})"`)
)

var categoryNames = map[string]string{
	"1010014": "英雄联盟", "1010045": "王者荣耀", "1010055": "金铲铲之战",
	"1010350": "魔兽争霸3", "1010032": "和平精英", "1011032": "三角洲行动",
	"1010092": "地下城与勇士",
	"3":       "单机游戏", "1": "射击游戏", "2": "竞技游戏",
	"105": "舞蹈", "106": "文化", "107": "生活", "108": "运动",
	"102": "音乐", "104": "二次元",
}

type rawRoom struct {
	rid, title, nickname, avatar string
}

type Platform struct{ platform.Base }

func (Platform) Name() string    { return "douyin" }
func (Platform) KeepStale() bool { return true }

func (Platform) FallbackURL(r core.Room) string { return fmt.Sprintf(playerBase, r.RID) }

func (Platform) Parse(line string) ([]core.Source, error) {
	t := strings.TrimSpace(line)
	if m := catRe.FindStringSubmatch(t); m != nil {
		return []core.Source{{Platform: "douyin", Kind: "category", Target: m[1]}}, nil
	}
	if m := roomRe.FindStringSubmatch(t); m != nil {
		return []core.Source{{Platform: "douyin", Kind: "room", Target: m[1]}}, nil
	}
	if numRe.MatchString(t) {
		return []core.Source{{Platform: "douyin", Kind: "room", Target: t}}, nil
	}
	return nil, nil
}

func splitCategory(path string) (partition, ptype string, err error) {
	var seg []string
	for _, s := range strings.Split(path, "_") {
		if s != "" {
			seg = append(seg, s)
		}
	}
	if len(seg) < 2 {
		return "", "", fmt.Errorf("无法识别分类路径: %s", path)
	}
	return seg[len(seg)-1], seg[len(seg)-2], nil
}

func apiParams(partition, ptype string, offset int) url.Values {
	q := url.Values{}
	q.Set("aid", "6383")
	q.Set("app_name", "douyin_web")
	q.Set("live_id", "1")
	q.Set("device_platform", "web")
	q.Set("language", "zh-CN")
	q.Set("cookie_enabled", "true")
	q.Set("screen_width", "1280")
	q.Set("screen_height", "720")
	q.Set("browser_language", "zh-CN")
	q.Set("browser_platform", "Windows")
	q.Set("browser_name", "Chrome")
	q.Set("browser_version", "151.0.0.0")
	q.Set("os_name", "Windows")
	q.Set("os_version", "10")
	q.Set("count", "15")
	q.Set("offset", strconv.Itoa(offset))
	q.Set("partition", partition)
	q.Set("partition_type", ptype)
	q.Set("req_from", "2")
	q.Set("a_bogus", aBogusParam)
	return q
}

func avatarFrom(v any) string {
	switch t := v.(type) {
	case map[string]any:
		if ul, ok := t["url_list"].([]any); ok && len(ul) > 0 {
			if s, ok := ul[0].(string); ok {
				return s
			}
		}
	case string:
		return t
	}
	return ""
}

func parseCategoryItem(it map[string]any) *rawRoom {
	room, _ := it["room"].(map[string]any)
	if room == nil {
		return nil
	}
	var owner map[string]any
	if o, ok := room["owner"].(map[string]any); ok {
		owner = o
	} else if o, ok := it["owner"].(map[string]any); ok {
		owner = o
	}
	rid := strings.TrimSpace(core.FirstStr(
		core.JStr(it, "web_rid"), core.JStr(room, "web_rid"), core.JStr(room, "webRid")))
	if !numRe.MatchString(rid) {
		return nil
	}
	avatar := ""
	cands := []any{it["avatar"]}
	if owner != nil {
		cands = append(cands, owner["avatar_thumb"], owner["avatar"])
	}
	cands = append(cands, room["cover"])
	for _, c := range cands {
		if a := avatarFrom(c); a != "" {
			avatar = a
			break
		}
	}
	nick := ""
	if owner != nil {
		nick = strings.TrimSpace(core.FirstStr(core.JStr(owner, "nickname"), core.JStr(owner, "nick_name")))
	}
	return &rawRoom{rid: rid, title: strings.TrimSpace(core.JStr(room, "title")), nickname: nick, avatar: avatar}
}

// httpFetchCategory 分类接口分页拉取（顺序翻页 + 间隔，避免风控）。
func httpFetchCategory(sess *core.Client, path string, pagesLimit int) ([]rawRoom, error) {
	partition, ptype, err := splitCategory(path)
	if err != nil {
		return nil, err
	}
	lastErr := ""
	for attempt := 0; attempt < categoryTry; attempt++ {
		if attempt > 0 {
			time.Sleep(retrySleep)
		}
		var rooms []rawRoom
		var failed bool
		for page := 0; page < pagesLimit; page++ {
			u := "https://live.douyin.com/webcast/web/partition/detail/room/v2/?" + apiParams(partition, ptype, page*15).Encode()
			st, raw, hdr, err := sess.Do("GET", u, nil, "", "https://live.douyin.com/categorynew/"+path, "", 0, nil)
			if err != nil {
				lastErr = core.FmtErr(err, 60)
				failed = true
				break
			}
			if _, ok := core.HasHeader(hdr, "bdturing-verify"); ok || len(raw) == 0 {
				lastErr = "触发风控"
				failed = true
				break
			}
			_ = st
			var j map[string]any
			if err := json.Unmarshal(raw, &j); err != nil {
				lastErr = core.FmtErr(err, 60)
				failed = true
				break
			}
			items := core.JList(core.JMap(j, "data"), "data")
			for _, it := range items {
				if m, ok := it.(map[string]any); ok {
					if r := parseCategoryItem(m); r != nil {
						rooms = append(rooms, *r)
					}
				}
			}
			if len(items) < 15 {
				break
			}
			time.Sleep(pageSleep)
		}
		if !failed && len(rooms) > 0 {
			return rooms, nil
		}
		if !failed {
			lastErr = "空数据"
		}
		core.Infof("  [接口] %s 第%d次尝试失败(%s), 重试", path, attempt+1, lastErr)
	}
	return nil, fmt.Errorf("分类接口重试%d次仍失败: %s", categoryTry, lastErr)
}

func httpFetchRoom(sess *core.Client, rid string) ([]rawRoom, error) {
	if _, _, _, err := sess.Do("GET", "https://live.douyin.com/"+rid, nil, "", "https://www.google.com/", "", 0, nil); err != nil {
		return nil, err
	}
	q := url.Values{}
	q.Set("aid", "6383")
	q.Set("app_name", "douyin_web")
	q.Set("live_id", "1")
	q.Set("device_platform", "web")
	q.Set("language", "zh-CN")
	q.Set("enter_from", "link_share")
	q.Set("cookie_enabled", "true")
	q.Set("screen_width", "1280")
	q.Set("screen_height", "720")
	q.Set("browser_language", "zh-CN")
	q.Set("browser_platform", "Windows")
	q.Set("browser_name", "Chrome")
	q.Set("browser_version", "151.0.0.0")
	q.Set("os_name", "Windows")
	q.Set("os_version", "10")
	q.Set("web_rid", rid)
	q.Set("room_id_str", "")
	q.Set("enter_source", "")
	q.Set("is_need_double_stream", "false")
	q.Set("insert_task_id", "")
	q.Set("live_reason", "")
	u := "https://live.douyin.com/webcast/room/web/enter/?" + q.Encode()
	_, raw, hdr, err := sess.Do("GET", u, nil, "", "https://live.douyin.com/"+rid, "application/json, text/plain, */*", 0, nil)
	if err != nil {
		return nil, err
	}
	if _, ok := core.HasHeader(hdr, "bdturing-verify"); ok || len(raw) == 0 {
		return nil, fmt.Errorf("触发风控 @ enter 接口")
	}
	var j map[string]any
	if err := json.Unmarshal(raw, &j); err != nil {
		return nil, err
	}
	d0 := core.JList(core.JMap(j, "data"), "data")
	if len(d0) == 0 {
		return nil, nil
	}
	d, _ := d0[0].(map[string]any)
	if d == nil {
		return nil, nil
	}
	user := core.JMap(d, "owner")
	if user == nil {
		user = core.JMap(d, "user")
	}
	avatar := avatarFrom(user["avatar_thumb"])
	if avatar == "" {
		avatar = avatarFrom(user["avatar"])
	}
	nick := ""
	if user != nil {
		nick = strings.TrimSpace(core.FirstStr(core.JStr(user, "nickname"), core.JStr(user, "nick_name")))
	}
	return []rawRoom{{rid: rid, title: strings.TrimSpace(core.JStr(d, "title")), nickname: nick, avatar: avatar}}, nil
}

// extractCategoryNames 从 RSC categoryData 分类树提取 {(type,id): 名}。
func extractCategoryNames(blob string) map[[2]string]string {
	out := map[[2]string]string{}
	i := strings.Index(blob, `"categoryData":`)
	if i < 0 {
		return out
	}
	j := strings.Index(blob[i:], "[")
	if j < 0 {
		return out
	}
	j += i
	depth := 0
	end := -1
	for k := j; k < len(blob); k++ {
		switch blob[k] {
		case '[':
			depth++
		case ']':
			depth--
			if depth == 0 {
				end = k + 1
				k = len(blob)
			}
		}
	}
	if end < 0 {
		return out
	}
	var arr []map[string]any
	if err := json.Unmarshal([]byte(blob[j:end]), &arr); err != nil {
		return out
	}
	var walk func(nodes []any)
	walk = func(nodes []any) {
		for _, n := range nodes {
			m, _ := n.(map[string]any)
			if m == nil {
				continue
			}
			part := core.JMap(m, "partition")
			if part != nil {
				tid := core.JStr(part, "id_str")
				ty := ""
				if v, ok := part["type"].(float64); ok {
					ty = strconv.Itoa(int(v))
				} else if s, ok := part["type"].(string); ok {
					ty = s
				}
				if tid != "" {
					if _, ok := out[[2]string{ty, tid}]; !ok {
						out[[2]string{ty, tid}] = strings.TrimSpace(core.JStr(part, "title"))
					}
				}
			}
			if sub, ok := m["sub_partition"].([]any); ok {
				walk(sub)
			}
		}
	}
	var top []any
	for _, n := range arr {
		top = append(top, n)
	}
	walk(top)
	return out
}

func categoryGroupName(path string, names map[[2]string]string) string {
	var seg []string
	for _, s := range strings.Split(path, "_") {
		if s != "" {
			seg = append(seg, s)
		}
	}
	var pairs [][2]string
	for i := 0; i+1 < len(seg); i += 2 {
		pairs = append(pairs, [2]string{seg[i], seg[i+1]})
	}
	if len(pairs) == 0 {
		return "douyin"
	}
	if names != nil {
		for i := len(pairs) - 1; i >= 0; i-- {
			if t := names[pairs[i]]; t != "" {
				return t
			}
		}
	}
	if n, ok := categoryNames[pairs[len(pairs)-1][1]]; ok {
		return n
	}
	return "douyin"
}

// extractObj 从 blob 中 idx 位置向前找 { 起点、向后找配对 }。
func extractObj(blob string, idx int) (string, bool) {
	depth := 0
	start := -1
	for i := idx - 1; i >= 0; i-- {
		switch blob[i] {
		case '}':
			depth++
		case '{':
			if depth == 0 {
				start = i
				i = -1
			} else {
				depth--
			}
		}
	}
	if start < 0 {
		return "", false
	}
	depth = 0
	for i := start; i < len(blob); i++ {
		switch blob[i] {
		case '{':
			depth++
		case '}':
			depth--
			if depth == 0 {
				return blob[start : i+1], true
			}
		}
	}
	return "", false
}

func parsePageHTML(html string) ([]rawRoom, map[[2]string]string, error) {
	var parts []string
	idx := 0
	for {
		loc := paceRe.FindStringIndex(html[idx:])
		if loc == nil {
			break
		}
		s := idx + loc[1]
		e := strings.Index(html[s:], `"])</script>`)
		if e < 0 {
			break
		}
		var decoded string
		if err := json.Unmarshal([]byte(`"`+html[s:s+e]+`"`), &decoded); err == nil {
			parts = append(parts, decoded)
		}
		idx = s + e + 1
	}
	blob := strings.Join(parts, "")
	if blob == "" {
		return nil, nil, fmt.Errorf("页面中未找到 RSC 数据")
	}
	var rooms []rawRoom
	seen := map[string]bool{}
	for _, m := range webRID.FindAllStringSubmatchIndex(blob, -1) {
		rid := blob[m[2]:m[3]]
		if seen[rid] {
			continue
		}
		objStr, ok := extractObj(blob, m[0])
		if !ok {
			continue
		}
		var obj map[string]any
		if err := json.Unmarshal([]byte(objStr), &obj); err != nil {
			continue
		}
		rm := core.JMap(obj, "room")
		if rm == nil {
			rm = obj
		}
		owner := core.JMap(rm, "owner")
		if owner == nil {
			owner = core.JMap(obj, "owner")
		}
		if owner == nil {
			owner = core.JMap(obj, "user")
		}
		avatar := ""
		if owner != nil {
			avatar = avatarFrom(owner["avatar_thumb"])
			if avatar == "" {
				avatar = avatarFrom(owner["avatar"])
			}
		}
		nick, title := "", ""
		if owner != nil {
			nick = strings.TrimSpace(core.FirstStr(core.JStr(owner, "nickname"), core.JStr(owner, "nick_name")))
		}
		title = strings.TrimSpace(core.JStr(rm, "title"))
		rooms = append(rooms, rawRoom{rid: rid, title: title, nickname: nick, avatar: avatar})
		seen[rid] = true
	}
	return rooms, extractCategoryNames(blob), nil
}

func httpFetchPage(kind, target string, sess *core.Client) ([]rawRoom, map[[2]string]string, error) {
	u := "https://live.douyin.com/" + target
	referer := "https://www.google.com/"
	if kind == "category" {
		u = "https://live.douyin.com/categorynew/" + target
		referer = ""
	}
	_, body, _, err := sess.GetText(u, referer)
	if err != nil {
		return nil, nil, err
	}
	return parsePageHTML(body)
}

func browserFetch(kind, target, projectRoot string) ([]rawRoom, error) {
	script := projectRoot + "/tools/browser_fetch_douyin.mjs"
	if _, err := os.Stat(script); err != nil {
		return nil, fmt.Errorf("缺少 tools/browser_fetch_douyin.mjs，跳过浏览器兜底")
	}
	if _, err := exec.LookPath("node"); err != nil {
		return nil, fmt.Errorf("缺少 Node，跳过浏览器兜底")
	}
	u := "https://live.douyin.com/" + target
	if kind == "category" {
		u = "https://live.douyin.com/categorynew/" + target
	}
	core.Infof("  [浏览器] %s 滚动加载中...", u)
	ctx, cancel := contextWithTimeout(browserTO)
	defer cancel()
	cmd := exec.CommandContext(ctx, "node", script, u)
	out, err := cmd.Output()
	if err != nil {
		var ee string
		if ee2, ok := err.(*exec.ExitError); ok {
			ee = string(ee2.Stderr)
			if len(ee) > 300 {
				ee = ee[len(ee)-300:]
			}
		}
		return nil, fmt.Errorf("浏览器兜底失败: %s", ee)
	}
	var rooms []rawRoom
	// 浏览器脚本输出 {rid,title,avatar,nickname,url}，url 忽略（统一走 Worker）。
	var arr []map[string]any
	if err := json.Unmarshal(out, &arr); err != nil {
		return nil, fmt.Errorf("浏览器兜底返回格式错误")
	}
	for _, m := range arr {
		rid := strings.TrimSpace(core.JStr(m, "rid"))
		if !numRe.MatchString(rid) {
			continue
		}
		rooms = append(rooms, rawRoom{
			rid: rid, title: strings.TrimSpace(core.JStr(m, "title")),
			nickname: strings.TrimSpace(core.JStr(m, "nickname")),
			avatar:   strings.TrimSpace(core.JStr(m, "avatar")),
		})
	}
	return rooms, nil
}

type fetchResult struct {
	rooms  []rawRoom
	method string
	group  string
	err    string
}

func fetchSource(kind, target string, sess *core.Client, hasHTTP bool, projectRoot string, pagesLimit int) fetchResult {
	group := "douyin"
	if kind == "category" {
		group = categoryGroupName(target, nil)
	}
	if hasHTTP {
		var rooms []rawRoom
		var err error
		if kind == "category" {
			rooms, err = httpFetchCategory(sess, target, pagesLimit)
		} else {
			rooms, err = httpFetchRoom(sess, target)
		}
		if err == nil && len(rooms) > 0 {
			return fetchResult{rooms, "接口", group, ""}
		}
		if err != nil {
			core.Infof("  [接口] %s: %s", target, core.FmtErr(err, 120))
		}
	} else {
		core.Infof("  [接口] ttwid 初始化失败，跳过接口层")
	}
	if kind == "category" {
		if rooms, err := browserFetch(kind, target, projectRoot); err == nil && len(rooms) > 0 {
			return fetchResult{rooms, "浏览器", group, ""}
		} else if err != nil {
			core.Infof("  [浏览器] %s: %s", target, core.FmtErr(err, 120))
		}
		if rooms, names, err := httpFetchPage(kind, target, sess); err == nil && len(rooms) > 0 {
			return fetchResult{rooms, "页面", categoryGroupName(target, names), ""}
		} else if err != nil {
			core.Infof("  [页面] %s: %s", target, core.FmtErr(err, 120))
		}
		return fetchResult{nil, "", "", "接口/浏览器/页面 三级均失败"}
	}
	if rooms, _, err := httpFetchPage(kind, target, sess); err == nil && len(rooms) > 0 {
		return fetchResult{rooms, "页面", group, ""}
	} else if err != nil {
		core.Infof("  [页面] %s: %s", target, core.FmtErr(err, 120))
	}
	if rooms, err := browserFetch(kind, target, projectRoot); err == nil {
		return fetchResult{rooms, "浏览器", group, ""}
	} else {
		return fetchResult{nil, "", "", core.FmtErr(err, 120)}
	}
}

func warmSession() (int, *core.Client, []*httpCookie) {
	sess := core.NewClient(nil)
	_, _, _, _ = sess.Do("GET", "https://live.douyin.com/categorynew/4_105", nil, "", "", "", 0, nil)
	var j map[string]any
	st, _, err := sess.PostJSON("https://ttwid.bytedance.com/ttwid/union/register/",
		map[string]any{"region": "cn", "aid": 6383, "needFid": false,
			"service":      "live.douyin.com",
			"migrate_info": map[string]any{"tier": "", "from_model": "pc"}},
		&j, "", "application/json", 0, nil)
	if err != nil {
		return 0, sess, nil
	}
	return st, sess, exportCookies(sess)
}

func (Platform) Fetch(sources []core.Source, ctx *core.Ctx) ([]core.Room, error) {
	core.Infof("[douyin] %d 个来源", len(sources))
	hasHTTP := true
	st, master, jarCookies := warmSession()
	if st != 200 {
		hasHTTP = false
		core.Warnf("[douyin] ttwid 初始化失败(status=%d)，跳过接口层", st)
		if master == nil {
			master = core.NewClient(nil)
		}
	} else {
		core.Infof("[douyin] ttwid 初始化完成, status=%d", st)
	}
	pagesLimit := maxPages
	if ctx.PagesCap > 0 && ctx.PagesCap < pagesLimit {
		pagesLimit = ctx.PagesCap
	}
	type task struct {
		kind, target string
	}
	tasks := make([]task, 0, len(sources))
	for _, s := range sources {
		tasks = append(tasks, task{s.Kind, s.Target})
	}
	results := core.Parallel(maxWorkers, tasks, func(t task) fetchResult {
		shard := core.NewClient(nil)
		importCookies(shard, jarCookies)
		return fetchSource(t.kind, t.target, shard, hasHTTP, ctx.ProjectRoot, pagesLimit)
	})
	var out []core.Room
	oks, fails := 0, 0
	for i, r := range results {
		src := sources[i]
		if r.err != "" || len(r.rooms) == 0 {
			fails++
			errMsg := r.err
			if errMsg == "" {
				errMsg = "空数据"
			}
			core.Warnf("[douyin] 全部失败 %s: %s", src.Target, errMsg)
			continue
		}
		oks++
		core.Infof("[douyin] [%s] %s: %d 个, group=%q", r.method, src.Target, len(r.rooms), r.group)
		for _, rm := range r.rooms {
			out = append(out, core.Room{
				Platform: "douyin", RID: rm.rid, Title: rm.title,
				Nickname: rm.nickname, Avatar: rm.avatar,
				URL: fmt.Sprintf(playerBase, rm.rid), Group: r.group,
			})
		}
	}
	core.Infof("[douyin] 完成: %d/%d 来源成功, %d 房间", oks, len(sources), len(out))
	return out, nil
}

var _ platform.Platform = Platform{}
