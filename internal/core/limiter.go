package core

import (
	"sync"
	"time"
)

// Limiter 全局最小请求间隔限速器（多 goroutine 共享，保证平均速率不超标）。
type Limiter struct {
	mu       sync.Mutex
	interval time.Duration
	last     time.Time
}

// NewLimiter 新建限速器，interval 为任意两次请求之间的最小间隔。
func NewLimiter(interval time.Duration) *Limiter {
	return &Limiter{interval: interval}
}

// Wait 阻塞到允许发起下一次请求。
func (l *Limiter) Wait() {
	l.mu.Lock()
	wait := l.interval - time.Since(l.last)
	if wait > 0 {
		time.Sleep(wait)
	}
	l.last = time.Now()
	l.mu.Unlock()
}
