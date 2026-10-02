package replay

import (
	"log"
	"strings"
	"time"

	"contextpath/streamer/internal/archive"
	"contextpath/streamer/internal/geometry"
	"contextpath/streamer/internal/model"
)

// defaultBatchSize caps a single tick's NATS message (matches the Python default).
const defaultBatchSize = 200

// logEvery emits a per-worker progress line at each N-event boundary (matches Python).
const logEvery = 100

// PublishFn ships one tick's batch of RawEvents (one NATS message per call).
type PublishFn func(events []model.RawEvent) error

// state is the running per-employee context accumulated across the stream.
type state struct {
	displays    []model.Display
	windowTitle *string
	process     *string
	clipboard   *string
	ocr         map[int]model.OCRPayload
	ocrOrder    []int // display ids in first-seen order (mirrors Python dict order)
}

// orElse replicates Python's `payload.field or state.field`: a non-empty override wins,
// otherwise the accumulated value is kept.
func orElse(override, fallback *string) *string {
	if override != nil && *override != "" {
		return override
	}
	return fallback
}

func (st *state) screenText() *string {
	var sb strings.Builder
	has := false
	for _, id := range st.ocrOrder {
		for _, b := range st.ocr[id].Blocks {
			if has {
				sb.WriteByte('\n')
			}
			sb.WriteString(b.Text)
			has = true
		}
	}
	if !has {
		return nil
	}
	s := sb.String()
	return &s
}

// advance applies an event to the context. Focus/OCR only mutate state; clicks and
// clipboard changes are the actionable events that yield a MatchContext.
func (st *state) advance(ev model.SourceEvent) (model.MatchContext, bool) {
	switch ev.Kind {
	case model.KindFocus:
		st.windowTitle = orElse(ev.Focus.WindowTitle, st.windowTitle)
		st.process = orElse(ev.Focus.ProcessName, st.process)
		return model.MatchContext{}, false
	case model.KindOCR:
		if _, seen := st.ocr[ev.OCR.DisplayID]; !seen {
			st.ocrOrder = append(st.ocrOrder, ev.OCR.DisplayID)
		}
		st.ocr[ev.OCR.DisplayID] = *ev.OCR
		return model.MatchContext{}, false
	case model.KindClipboard:
		st.clipboard = ev.Clipboard.ContentText
		return model.MatchContext{
			Process:     orElse(ev.Clipboard.SourceApp, st.process),
			WindowTitle: st.windowTitle,
			Clipboard:   st.clipboard,
			OCR:         st.screenText(),
		}, true
	default: // click
		return model.MatchContext{
			Click:       geometry.ResolveClickText(ev.Click.MouseX, ev.Click.MouseY, st.displays, st.ocr),
			Process:     orElse(ev.Click.ProcessName, st.process),
			WindowTitle: orElse(ev.Click.WindowTitle, st.windowTitle),
			Clipboard:   st.clipboard,
			OCR:         st.screenText(),
		}, true
	}
}

// Run replays one archive to its employee topic. The one-second tick is the unit of both
// batching and pacing: all actionable events in a tick ship as one message, and between
// two populated seconds the worker consumes `delta` ticker beats (delta * TICK_MS),
// collapsing idle gaps into a single proportional pause. Returns RawEvents published.
func Run(rep *archive.Replay, employee string, publish PublishFn, tickMS int) (int, error) {
	st := &state{displays: rep.Displays, ocr: map[int]model.OCRPayload{}}
	buffer := make([]model.RawEvent, 0, defaultBatchSize)
	published := 0
	loggedMilestone := 0 // last logEvery boundary already reported

	var prevTick int64
	havePrev := false

	ticker := time.NewTicker(time.Duration(tickMS) * time.Millisecond)
	defer ticker.Stop()

	flush := func() error {
		if len(buffer) == 0 {
			return nil
		}
		if err := publish(buffer); err != nil {
			return err
		}
		published += len(buffer)
		buffer = buffer[:0]
		// Progress only at each logEvery boundary — cheap, off the per-event path. The
		// archive streams lazily (unknown total up front), so log the running count.
		if published/logEvery > loggedMilestone {
			loggedMilestone = published / logEvery
			log.Printf("[%s] progress: published %d events", employee, published)
		}
		return nil
	}

	for {
		ev, ok := rep.Next()
		if !ok {
			break
		}
		tick := ev.TS.Unix()
		if havePrev && tick != prevTick {
			if err := flush(); err != nil {
				return published, err
			}
			for i := int64(0); i < tick-prevTick; i++ {
				<-ticker.C
			}
		}
		prevTick = tick
		havePrev = true

		ctx, actionable := st.advance(ev)
		if actionable {
			buffer = append(buffer, model.RawEvent{Employee: employee, TS: ev.TS, Context: ctx})
			if len(buffer) >= defaultBatchSize {
				if err := flush(); err != nil {
					return published, err
				}
			}
		}
	}
	if err := flush(); err != nil {
		return published, err
	}
	return published, nil
}
