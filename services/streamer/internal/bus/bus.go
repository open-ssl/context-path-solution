package bus

import (
	"encoding/json"
	"log"
	"time"

	"github.com/nats-io/nats.go"

	"contextpath/streamer/internal/model"
)

// Connect dials NATS with a fixed retry/backoff loop (mirrors the Python client).
func Connect(url string, retries int, delay time.Duration) (*nats.Conn, error) {
	var lastErr error
	for attempt := 1; attempt <= retries; attempt++ {
		nc, err := nats.Connect(url)
		if err == nil {
			log.Printf("connected to NATS at %s", url)
			return nc, nil
		}
		lastErr = err
		log.Printf("NATS not ready (attempt %d/%d): %v", attempt, retries, err)
		time.Sleep(delay)
	}
	return nil, lastErr
}

// PublishBatch sends a whole interval's RawEvents as one JSON-array message, matching the
// Python RawEventBatch wire format consumed by the agent.
func PublishBatch(nc *nats.Conn, subject string, events []model.RawEvent) error {
	data, err := json.Marshal(events)
	if err != nil {
		return err
	}
	return nc.Publish(subject, data)
}
