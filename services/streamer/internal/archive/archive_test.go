package archive

import (
	"archive/tar"
	"compress/gzip"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"contextpath/streamer/internal/model"
)

const metaJSON = `{"employee_id":"emp-1","machine":{"displays":[]}}`

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

func drainOCR(t *testing.T, path string) []model.OCRPayload {
	t.Helper()
	rep, err := Open(path)
	if err != nil {
		t.Fatalf("Open: %v", err)
	}
	defer rep.Close()
	var out []model.OCRPayload
	for {
		ev, ok := rep.Next()
		if !ok {
			break
		}
		if ev.Kind == model.KindOCR {
			out = append(out, *ev.OCR)
		}
	}
	return out
}

func TestOpen_ResolvesDeduplicatedFrom(t *testing.T) {
	// A canonical frame carries the text; a later frame references it by filename and
	// ships no blocks of its own. The resolved text must be substituted from the cache.
	ocr := strings.Join([]string{
		`{"ts":"2026-03-10T16:00:00Z","filename":"frame_1.jpg","display_id":0,"resolution":[100,100],"ocr_text_blocks":[{"text":"Canonical text","bounding_box":[0,0,10,10]}],"deduplicated_from":null}`,
		`{"ts":"2026-03-10T16:00:01Z","filename":"frame_2.jpg","display_id":0,"ocr_text_blocks":[],"deduplicated_from":"frame_1.jpg"}`,
	}, "\n")

	ocrEvents := drainOCR(t, writeArchive(t, map[string]string{
		"metadata.json":    metaJSON,
		"events/ocr.jsonl": ocr,
	}))

	if len(ocrEvents) != 2 {
		t.Fatalf("want 2 ocr events, got %d", len(ocrEvents))
	}
	canonical, dedup := ocrEvents[0], ocrEvents[1]
	if len(canonical.Blocks) != 1 || canonical.Blocks[0].Text != "Canonical text" {
		t.Fatalf("canonical frame lost its text: %+v", canonical.Blocks)
	}
	if len(dedup.Blocks) != 1 || dedup.Blocks[0].Text != "Canonical text" {
		t.Fatalf("deduplicated frame not resolved from cache: %+v", dedup.Blocks)
	}
}

func TestOpen_UnresolvedDedupKeepsEmptyBlocks(t *testing.T) {
	// Reference to an unknown filename cannot be resolved; blocks stay empty (no panic).
	ocr := `{"ts":"2026-03-10T16:00:00Z","filename":"frame_2.jpg","display_id":0,"ocr_text_blocks":[],"deduplicated_from":"missing.jpg"}`
	ocrEvents := drainOCR(t, writeArchive(t, map[string]string{
		"metadata.json":    metaJSON,
		"events/ocr.jsonl": ocr,
	}))
	if len(ocrEvents) != 1 {
		t.Fatalf("want 1 ocr event, got %d", len(ocrEvents))
	}
	if len(ocrEvents[0].Blocks) != 0 {
		t.Fatalf("want empty blocks, got %+v", ocrEvents[0].Blocks)
	}
}

func TestOpen_MissingMetadataErrors(t *testing.T) {
	path := writeArchive(t, map[string]string{"events/ocr.jsonl": "{}"})
	if _, err := Open(path); err == nil {
		t.Fatal("expected an error when metadata.json is missing")
	}
}
