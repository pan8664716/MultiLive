package core

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/cookiejar"
	"time"
)

// Client 带 CookieJar 的 HTTP 会话（每个 goroutine 独立一个实例）。
type Client struct {
	HTTP    *http.Client
	Headers map[string]string
}

// NewClient 新建会话，extra 合并到默认头（UA/Accept-Language）。
func NewClient(extra map[string]string) *Client {
	jar, _ := cookiejar.New(nil)
	h := map[string]string{
		"User-Agent":      UAChrome,
		"Accept-Language": "zh-CN,zh;q=0.9",
	}
	for k, v := range extra {
		h[k] = v
	}
	return &Client{
		HTTP:    &http.Client{Jar: jar, Timeout: 30 * time.Second},
		Headers: h,
	}
}

// Do 发起请求，返回 (状态码, 响应体, 响应头)。
func (c *Client) Do(method, url string, body []byte, contentType, referer, accept string, timeout time.Duration, extra map[string]string) (int, []byte, http.Header, error) {
	var rdr io.Reader
	if body != nil {
		rdr = bytes.NewReader(body)
	}
	req, err := http.NewRequest(method, url, rdr)
	if err != nil {
		return 0, nil, nil, err
	}
	for k, v := range c.Headers {
		req.Header.Set(k, v)
	}
	for k, v := range extra {
		req.Header.Set(k, v)
	}
	if accept != "" {
		req.Header.Set("Accept", accept)
	}
	if referer != "" {
		req.Header.Set("Referer", referer)
	}
	if body != nil {
		ct := contentType
		if ct == "" {
			ct = "application/json"
		}
		req.Header.Set("Content-Type", ct)
	}
	client := c.HTTP
	if timeout > 0 {
		cp := *c.HTTP
		cp.Timeout = timeout
		client = &cp
	}
	resp, err := client.Do(req)
	if err != nil {
		return 0, nil, nil, err
	}
	defer resp.Body.Close()
	raw, err := io.ReadAll(io.LimitReader(resp.Body, 32<<20))
	if err != nil {
		return resp.StatusCode, nil, resp.Header, err
	}
	return resp.StatusCode, raw, resp.Header, nil
}

// GetText GET 文本。
func (c *Client) GetText(url, referer string) (int, string, http.Header, error) {
	st, raw, hdr, err := c.Do("GET", url, nil, "", referer, "", 0, nil)
	if err != nil {
		return st, "", hdr, err
	}
	return st, string(raw), hdr, nil
}

// GetJSON GET 并解析 JSON（v 为 map 或任意指针）。
func (c *Client) GetJSON(url, referer string, v any, timeout time.Duration) (int, http.Header, error) {
	st, raw, hdr, err := c.Do("GET", url, nil, "", referer, "", timeout, nil)
	if err != nil {
		return st, hdr, err
	}
	if st < 200 || st >= 300 {
		return st, hdr, fmt.Errorf("HTTP %d: %s", st, truncate(raw, 120))
	}
	if err := json.Unmarshal(raw, v); err != nil {
		return st, hdr, fmt.Errorf("非JSON响应(status=%d): %s", st, truncate(raw, 120))
	}
	return st, hdr, nil
}

// GetJSONHeaders 同 GetJSON 但额外返回响应头（风控头检测用）。
func (c *Client) GetJSONHeaders(url, referer string, v any, timeout time.Duration) (int, []byte, http.Header, error) {
	st, raw, hdr, err := c.Do("GET", url, nil, "", referer, "", timeout, nil)
	if err != nil {
		return st, nil, hdr, err
	}
	if st < 200 || st >= 300 {
		return st, raw, hdr, fmt.Errorf("HTTP %d: %s", st, truncate(raw, 120))
	}
	if err := json.Unmarshal(raw, v); err != nil {
		return st, raw, hdr, fmt.Errorf("非JSON响应(status=%d): %s", st, truncate(raw, 120))
	}
	return st, raw, hdr, nil
}

// PostJSON POST JSON 并解析 JSON 响应。
func (c *Client) PostJSON(url string, payload any, v any, referer, accept string, timeout time.Duration, extra map[string]string) (int, http.Header, error) {
	body, err := json.Marshal(payload)
	if err != nil {
		return 0, nil, err
	}
	st, raw, hdr, err := c.Do("POST", url, body, "application/json", referer, accept, timeout, extra)
	if err != nil {
		return st, hdr, err
	}
	if st < 200 || st >= 300 {
		return st, hdr, fmt.Errorf("HTTP %d: %s", st, truncate(raw, 120))
	}
	if err := json.Unmarshal(raw, v); err != nil {
		return st, hdr, fmt.Errorf("非JSON响应(status=%d): %s", st, truncate(raw, 120))
	}
	return st, hdr, nil
}

// HasHeader 不区分大小写检查响应头是否存在（如 bdturing-verify 风控头）。
func HasHeader(hdr http.Header, key string) (string, bool) {
	for k, vs := range hdr {
		if len(k) == len(key) && equalFold(k, key) && len(vs) > 0 {
			return vs[0], true
		}
	}
	return "", false
}

func equalFold(a, b string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := 0; i < len(a); i++ {
		ca, cb := a[i], b[i]
		if ca >= 'A' && ca <= 'Z' {
			ca += 'a' - 'A'
		}
		if cb >= 'A' && cb <= 'Z' {
			cb += 'a' - 'A'
		}
		if ca != cb {
			return false
		}
	}
	return true
}

func truncate(b []byte, n int) string {
	r := []rune(string(b))
	if len(r) > n {
		return string(r[:n])
	}
	return string(r)
}
