package model

import "time"

// Kind tags a decoded source event so the replay state machine can route it.
type Kind int

const (
	KindFocus Kind = iota
	KindClick
	KindClipboard
	KindOCR
)

// OCRBlock is a single recognized text region with its pixel-space bounding box.
type OCRBlock struct {
	Text string
	BBox [4]int // x, y, w, h
}

// OCRPayload is one OCR snapshot for a display (deduplicated_from already resolved).
type OCRPayload struct {
	DisplayID  int
	Resolution *[2]int // snapshot pixel space; nil => use display bounds
	Blocks     []OCRBlock
}

type FocusPayload struct {
	WindowTitle *string
	ProcessName *string
}

type ClickPayload struct {
	MouseX      int
	MouseY      int
	WindowTitle *string
	ProcessName *string
}

type ClipboardPayload struct {
	ContentText *string
	SourceApp   *string
}

// SourceEvent is a single decoded recorder event in the merged chronological stream.
type SourceEvent struct {
	TS        time.Time
	Kind      Kind
	Focus     *FocusPayload
	Click     *ClickPayload
	Clipboard *ClipboardPayload
	OCR       *OCRPayload
}

// Display mirrors the metadata.json display bounds used for click↔OCR correlation.
type Display struct {
	ID     int
	Bounds [4]int // x, y, w, h
}

// MatchContext is the rule-engine context. Field names and null semantics match the
// Python Pydantic model so the agent decodes a Go batch byte-for-byte compatibly.
type MatchContext struct {
	Click       *string `json:"click"`
	Process     *string `json:"process"`
	WindowTitle *string `json:"window_title"`
	Clipboard   *string `json:"clipboard"`
	OCR         *string `json:"ocr"`
}

// RawEvent is the `events.raw.*` wire contract: a context tagged with its origin.
type RawEvent struct {
	Employee string       `json:"employee"`
	TS       time.Time    `json:"ts"`
	Context  MatchContext `json:"context"`
}
