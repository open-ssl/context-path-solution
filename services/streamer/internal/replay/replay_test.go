package replay

import (
	"archive/tar"
	"compress/gzip"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"contextpath/streamer/internal/archive"
	"contextpath/streamer/internal/model"
)

const metaJSON = `{"employee_id":"emp-1","machine":{"displays":[]}}`

func ts(sec int) string { return "2026-03-10T16:00:0" + string(rune('0'+sec)) + "Z" }

// Four per-file streams, each internally sorted by ts but globally interleaved, so a
// correct K-way merge must re-interleave them into one chronological stream:
//
//	ocr 0 · click 1 · focus 2 · clip 3 · click 4 · focus 5 · clip 6 · click 7 · ocr 8
func interleavedArchive(t *testing.T) string {
	t.Helper()
	focus := strings.Join([]string{
		`{"ts":"` + ts(2) + `","event":"focus_change","window_title":"W","process_name":"P"}`,
		`{"ts":"` + ts(5) + `","event":"focus_change","window_title":"W","process_name":"P"}`,
	}, "\n")
	click := strings.Join([]string{
		`{"ts":"` + ts(1) + `","event":"click","mouse_x":1,"mouse_y":1}`,
		`{"ts":"` + ts(4) + `","event":"click","mouse_x":1,"mouse_y":1}`,
		`{"ts":"` + ts(7) + `","event":"click","mouse_x":1,"mouse_y":1}`,
	}, "\n")
	clip := strings.Join([]string{
		`{"ts":"` + ts(3) + `","event":"clipboard_change","clipboard_content_text":"A"}`,
		`{"ts":"` + ts(6) + `","event":"clipboard_change","clipboard_content_text":"B"}`,
	}, "\n")
	ocr := strings.Join([]string{
		`{"ts":"` + ts(0) + `","filename":"f0","display_id":0,"ocr_text_blocks":[]}`,
		`{"ts":"` + ts(8) + `","filename":"f1","display_id":0,"ocr_text_blocks":[]}`,
	}, "\n")
	return writeArchive(t, map[string]string{
		"metadata.json":          metaJSON,
		"events/windows.jsonl":   focus,
		"events/mouse.jsonl":     click,
		"events/clipboard.jsonl": clip,
		"events/ocr.jsonl":       ocr,
	})
}

func TestKWayMergeIsChronological(t *testing.T) {
	rep, err := archive.Open(interleavedArchive(t))
	if err != nil {
		t.Fatalf("Open: %v", err)
	}
	defer rep.Close()

	var secs []int
	var prev time.Time
	for i := 0; ; i++ {
		ev, ok := rep.Next()
		if !ok {
			break
		}
		if i > 0 && ev.TS.Before(prev) {
			t.Fatalf("event %d out of order: %s before %s", i, ev.TS, prev)
		}
		prev = ev.TS
		secs = append(secs, ev.TS.Second())
	}
	if want := []int{0, 1, 2, 3, 4, 5, 6, 7, 8}; !equalInts(secs, want) {
		t.Fatalf("merged order = %v, want %v", secs, want)
	}
}

func TestRunEmitsActionableEventsInOrder(t *testing.T) {
	rep, err := archive.Open(interleavedArchive(t))
	if err != nil {
		t.Fatalf("Open: %v", err)
	}
	defer rep.Close()

	var got []model.RawEvent
	publish := func(events []model.RawEvent) error {
		got = append(got, events...)
		return nil
	}

	n, err := Run(rep, "emp-1", publish, 1)
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	if n != len(got) {
		t.Fatalf("returned count %d != collected %d", n, len(got))
	}

	// Only clicks (1,4,7) and clipboard changes (3,6) are actionable; focus/ocr are not.
	var secs []int
	var prev time.Time
	for i, e := range got {
		if e.Employee != "emp-1" {
			t.Fatalf("event %d has employee %q, want emp-1", i, e.Employee)
		}
		if i > 0 && e.TS.Before(prev) {
			t.Fatalf("published event %d out of order", i)
		}
		prev = e.TS
		secs = append(secs, e.TS.Second())
	}
	if want := []int{1, 3, 4, 6, 7}; !equalInts(secs, want) {
		t.Fatalf("published order = %v, want %v", secs, want)
	}
}

// writeArchive builds a throwaway .tar.gz from name->content and returns its path.
func writeArchive(t *testing.T, files map[string]string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "emp.tar.gz")
	f, err := os.Create(path)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	gz := gzip.NewWriter(f)
	tw := tar.NewWriter(gz)
	for name, content := range files {
		if err := tw.WriteHeader(&tar.Header{Name: name, Mode: 0o644, Size: int64(len(content))}); err != nil {
			t.Fatal(err)
		}
		if _, err := tw.Write([]byte(content)); err != nil {
			t.Fatal(err)
		}
	}
	if err := tw.Close(); err != nil {
		t.Fatal(err)
	}
	if err := gz.Close(); err != nil {
		t.Fatal(err)
	}
	return path
}

func equalInts(a, b []int) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}
