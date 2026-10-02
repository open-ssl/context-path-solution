package archive

import (
	"archive/tar"
	"bufio"
	"bytes"
	"compress/gzip"
	"container/heap"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"contextpath/streamer/internal/model"
)

// OCR lines carry many blocks and can exceed bufio.Scanner's default 64 KiB token.
const maxLineBytes = 16 * 1024 * 1024

// Mouse-move events dominate mouse.jsonl but never drive a rule. Reject them with a raw
// byte-substring check before JSON decoding (recorder emits a space after the colon).
var moveMarker = []byte(`"event": "move"`)

// Relevant event files in a fixed order. The order doubles as the tie-break for equal
// timestamps in the K-way merge, matching Python's stable heapq.merge.
var eventFiles = []struct {
	name string
	kind model.Kind
}{
	{"windows.jsonl", model.KindFocus},
	{"mouse.jsonl", model.KindClick},
	{"clipboard.jsonl", model.KindClipboard},
	{"ocr.jsonl", model.KindOCR},
}

type rawMeta struct {
	EmployeeID string `json:"employee_id"`
	Machine    struct {
		Displays []struct {
			ID     int    `json:"id"`
			Bounds [4]int `json:"bounds"`
		} `json:"displays"`
	} `json:"machine"`
}

// Replay streams one employee archive's events in chronological order. It is lazy: at
// most one row per open member file is held in memory (O(K) for K files).
type Replay struct {
	Employee string
	Displays []model.Display
	next     func() (model.SourceEvent, bool)
	closers  []func()
}

func (r *Replay) Next() (model.SourceEvent, bool) { return r.next() }

func (r *Replay) Close() {
	for _, c := range r.closers {
		c()
	}
}

// Discover lists `*.tar.gz` archives in dir, sorted by name (one archive per employee).
func Discover(dir string) ([]string, error) {
	paths, err := filepath.Glob(filepath.Join(dir, "*.tar.gz"))
	if err != nil {
		return nil, err
	}
	sort.Strings(paths)
	return paths, nil
}

// scan reads metadata.json and records which relevant event members are present, without
// streaming their bodies.
func scan(path string) (rawMeta, map[string]string, error) {
	f, err := os.Open(path)
	if err != nil {
		return rawMeta{}, nil, err
	}
	defer f.Close()
	gz, err := gzip.NewReader(f)
	if err != nil {
		return rawMeta{}, nil, err
	}
	defer gz.Close()

	tr := tar.NewReader(gz)
	var meta rawMeta
	haveMeta := false
	members := map[string]string{}
	for {
		hdr, err := tr.Next()
		if err == io.EOF {
			break
		}
		if err != nil {
			return rawMeta{}, nil, err
		}
		if hdr.FileInfo().IsDir() {
			continue
		}
		base := filepath.Base(hdr.Name)
		if base == "metadata.json" {
			if err := json.NewDecoder(tr).Decode(&meta); err != nil {
				return rawMeta{}, nil, fmt.Errorf("decode metadata.json: %w", err)
			}
			haveMeta = true
			continue
		}
		for _, ef := range eventFiles {
			if base == ef.name {
				members[ef.name] = hdr.Name
			}
		}
	}
	if !haveMeta {
		return rawMeta{}, nil, fmt.Errorf("archive %s is missing metadata.json", path)
	}
	return meta, members, nil
}

// openMember opens the archive afresh and advances to a single member, returning a reader
// positioned at its body. A fresh handle per member lets K members be streamed in parallel.
func openMember(path, memberName string) (*tar.Reader, func(), error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, nil, err
	}
	gz, err := gzip.NewReader(f)
	if err != nil {
		f.Close()
		return nil, nil, err
	}
	tr := tar.NewReader(gz)
	for {
		hdr, err := tr.Next()
		if err != nil {
			gz.Close()
			f.Close()
			return nil, nil, fmt.Errorf("member %s: %w", memberName, err)
		}
		if hdr.Name == memberName {
			return tr, func() { gz.Close(); f.Close() }, nil
		}
	}
}

// stream is one chronological per-file source: a scanner plus the per-kind decoder and,
// for OCR, the running canonical map that resolves `deduplicated_from`.
type stream struct {
	sc        *bufio.Scanner
	kind      model.Kind
	dropMoves bool
	canonical map[string][]model.OCRBlock
	closer    func()
}

func newStream(path, memberName string, kind model.Kind) (*stream, error) {
	tr, closer, err := openMember(path, memberName)
	if err != nil {
		return nil, err
	}
	sc := bufio.NewScanner(tr)
	sc.Buffer(make([]byte, 0, 64*1024), maxLineBytes)
	s := &stream{sc: sc, kind: kind, dropMoves: kind == model.KindClick, closer: closer}
	if kind == model.KindOCR {
		s.canonical = map[string][]model.OCRBlock{}
	}
	return s, nil
}

func (s *stream) next() (model.SourceEvent, bool) {
	for s.sc.Scan() {
		line := s.sc.Bytes()
		if s.dropMoves && bytes.Contains(line, moveMarker) {
			continue
		}
		trimmed := bytes.TrimSpace(line)
		if len(trimmed) == 0 {
			continue
		}
		if ev, ok := s.parse(trimmed); ok {
			return ev, true
		}
	}
	return model.SourceEvent{}, false
}

func parseTS(s string) (time.Time, bool) {
	t, err := time.Parse(time.RFC3339Nano, s)
	if err != nil {
		return time.Time{}, false
	}
	return t, true
}

func (s *stream) parse(line []byte) (model.SourceEvent, bool) {
	switch s.kind {
	case model.KindFocus:
		var r struct {
			TS          string  `json:"ts"`
			Event       string  `json:"event"`
			WindowTitle *string `json:"window_title"`
			ProcessName *string `json:"process_name"`
		}
		if json.Unmarshal(line, &r) != nil || r.Event != "focus_change" {
			return model.SourceEvent{}, false
		}
		ts, ok := parseTS(r.TS)
		if !ok {
			return model.SourceEvent{}, false
		}
		return model.SourceEvent{TS: ts, Kind: model.KindFocus,
			Focus: &model.FocusPayload{WindowTitle: r.WindowTitle, ProcessName: r.ProcessName}}, true

	case model.KindClick:
		var r struct {
			TS          string  `json:"ts"`
			Event       string  `json:"event"`
			MouseX      int     `json:"mouse_x"`
			MouseY      int     `json:"mouse_y"`
			WindowTitle *string `json:"window_title"`
			ProcessName *string `json:"process_name"`
		}
		if json.Unmarshal(line, &r) != nil || r.Event != "click" {
			return model.SourceEvent{}, false
		}
		ts, ok := parseTS(r.TS)
		if !ok {
			return model.SourceEvent{}, false
		}
		return model.SourceEvent{TS: ts, Kind: model.KindClick,
			Click: &model.ClickPayload{MouseX: r.MouseX, MouseY: r.MouseY, WindowTitle: r.WindowTitle, ProcessName: r.ProcessName}}, true

	case model.KindClipboard:
		var r struct {
			TS          string  `json:"ts"`
			Event       string  `json:"event"`
			ContentText *string `json:"clipboard_content_text"`
			SourceApp   *string `json:"clipboard_source_app"`
		}
		if json.Unmarshal(line, &r) != nil || r.Event != "clipboard_change" {
			return model.SourceEvent{}, false
		}
		ts, ok := parseTS(r.TS)
		if !ok {
			return model.SourceEvent{}, false
		}
		return model.SourceEvent{TS: ts, Kind: model.KindClipboard,
			Clipboard: &model.ClipboardPayload{ContentText: r.ContentText, SourceApp: r.SourceApp}}, true

	case model.KindOCR:
		var r struct {
			TS               string  `json:"ts"`
			Filename         string  `json:"filename"`
			DisplayID        int     `json:"display_id"`
			Resolution       *[2]int `json:"resolution"`
			DeduplicatedFrom *string `json:"deduplicated_from"`
			Blocks           []struct {
				Text string `json:"text"`
				BBox [4]int `json:"bounding_box"`
			} `json:"ocr_text_blocks"`
		}
		if json.Unmarshal(line, &r) != nil {
			return model.SourceEvent{}, false
		}
		ts, ok := parseTS(r.TS)
		if !ok {
			return model.SourceEvent{}, false
		}
		blocks := make([]model.OCRBlock, 0, len(r.Blocks))
		for _, b := range r.Blocks {
			blocks = append(blocks, model.OCRBlock{Text: b.Text, BBox: b.BBox})
		}
		// Resolve deduplication on the fly: a canonical frame (with blocks) always
		// precedes the frames that reference it by filename.
		if len(blocks) > 0 {
			if r.Filename != "" {
				if _, seen := s.canonical[r.Filename]; !seen {
					s.canonical[r.Filename] = blocks
				}
			}
		} else if r.DeduplicatedFrom != nil {
			if c, ok := s.canonical[*r.DeduplicatedFrom]; ok {
				blocks = c
			}
		}
		return model.SourceEvent{TS: ts, Kind: model.KindOCR,
			OCR: &model.OCRPayload{DisplayID: r.DisplayID, Resolution: r.Resolution, Blocks: blocks}}, true
	}
	return model.SourceEvent{}, false
}

// --- K-way merge via a min-heap keyed by ts (tie-break by stream index) ---

type heapItem struct {
	ev  model.SourceEvent
	idx int
}

type eventHeap []heapItem

func (h eventHeap) Len() int { return len(h) }
func (h eventHeap) Less(i, j int) bool {
	if h[i].ev.TS.Equal(h[j].ev.TS) {
		return h[i].idx < h[j].idx
	}
	return h[i].ev.TS.Before(h[j].ev.TS)
}
func (h eventHeap) Swap(i, j int) { h[i], h[j] = h[j], h[i] }
func (h *eventHeap) Push(x any)   { *h = append(*h, x.(heapItem)) }
func (h *eventHeap) Pop() any {
	old := *h
	n := len(old)
	it := old[n-1]
	*h = old[:n-1]
	return it
}

// Open prepares an archive for lazy streaming: reads metadata, opens each present event
// member on its own handle, and returns a merged chronological iterator.
func Open(path string) (*Replay, error) {
	meta, members, err := scan(path)
	if err != nil {
		return nil, err
	}

	employee := meta.EmployeeID
	if employee == "" {
		employee = strings.TrimSuffix(filepath.Base(path), ".tar.gz")
	}
	displays := make([]model.Display, 0, len(meta.Machine.Displays))
	for _, d := range meta.Machine.Displays {
		displays = append(displays, model.Display{ID: d.ID, Bounds: d.Bounds})
	}

	var streams []*stream
	var closers []func()
	for _, ef := range eventFiles {
		mn, ok := members[ef.name]
		if !ok {
			continue
		}
		st, err := newStream(path, mn, ef.kind)
		if err != nil {
			for _, c := range closers {
				c()
			}
			return nil, err
		}
		streams = append(streams, st)
		closers = append(closers, st.closer)
	}

	h := &eventHeap{}
	heap.Init(h)
	for i, st := range streams {
		if ev, ok := st.next(); ok {
			heap.Push(h, heapItem{ev: ev, idx: i})
		}
	}

	next := func() (model.SourceEvent, bool) {
		if h.Len() == 0 {
			return model.SourceEvent{}, false
		}
		it := heap.Pop(h).(heapItem)
		if ev, ok := streams[it.idx].next(); ok {
			heap.Push(h, heapItem{ev: ev, idx: it.idx})
		}
		return it.ev, true
	}

	return &Replay{Employee: employee, Displays: displays, next: next, closers: closers}, nil
}
