package main

import (
	"log"
	"os"
	"runtime"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"contextpath/streamer/internal/archive"
	"contextpath/streamer/internal/bus"
	"contextpath/streamer/internal/config"
	"contextpath/streamer/internal/model"
	"contextpath/streamer/internal/replay"
)

const subjectPrefix = "events.raw"

// NATS subject tokens cannot contain dots or spaces; sanitize the employee id for the
// topic only (the RawEvent payload keeps the original id the agent stores).
var subjectSanitizer = strings.NewReplacer(".", "_", " ", "_")

func main() {
	log.SetFlags(log.LstdFlags | log.Lmicroseconds)
	log.Printf("streamer-go starting (GOMAXPROCS=%d, NumCPU=%d)", runtime.GOMAXPROCS(0), runtime.NumCPU())

	cfg := config.Load()

	paths, err := archive.Discover(cfg.DataDir)
	if err != nil {
		log.Fatalf("discover archives in %s: %v", cfg.DataDir, err)
	}
	log.Printf("discovered %d archive(s) in %s", len(paths), cfg.DataDir)
	if len(paths) == 0 {
		log.Printf("no archives found in %s; nothing to replay", cfg.DataDir)
		os.Exit(0)
	}

	nc, err := bus.Connect(cfg.NATSURL, 10, time.Second)
	if err != nil {
		log.Fatalf("could not connect to NATS at %s: %v", cfg.NATSURL, err)
	}

	var total int64
	var wg sync.WaitGroup
	startAll := time.Now()
	for _, p := range paths {
		wg.Add(1)
		go func(path string) {
			defer wg.Done()

			rep, err := archive.Open(path)
			if err != nil {
				log.Printf("skipping archive %s: %v", path, err)
				return
			}
			defer rep.Close()

			subject := subjectPrefix + "." + subjectSanitizer.Replace(rep.Employee)
			log.Printf("[%s] worker started replay (%s -> %s)", rep.Employee, path, subject)

			publish := func(events []model.RawEvent) error {
				return bus.PublishBatch(nc, subject, events)
			}
			start := time.Now()
			n, err := replay.Run(rep, rep.Employee, publish, cfg.TickMS)
			if err != nil {
				log.Printf("[%s] replay error: %v", rep.Employee, err)
			}
			atomic.AddInt64(&total, int64(n))
			log.Printf("replay complete for %s: published %d RawEvent(s) in %s", rep.Employee, n, time.Since(start).Round(time.Millisecond))
		}(p)
	}
	wg.Wait()

	if err := nc.Flush(); err != nil {
		log.Printf("nats flush failed: %v", err)
	}
	nc.Close()

	elapsed := time.Since(startAll)
	grandTotal := atomic.LoadInt64(&total)
	rate := 0.0
	if s := elapsed.Seconds(); s > 0 {
		rate = float64(grandTotal) / s
	}
	log.Printf("streamer-go finished: %d RawEvent(s) from %d archive(s) published under %s.* in %s (%.0f events/s)",
		grandTotal, len(paths), subjectPrefix, elapsed.Round(time.Millisecond), rate)
	os.Exit(0)
}
