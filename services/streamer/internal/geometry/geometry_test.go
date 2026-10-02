package geometry

import (
	"testing"

	"contextpath/streamer/internal/model"
)

func res(w, h int) *[2]int { return &[2]int{w, h} }

// One display at the origin, 1:1 OCR resolution, a single text block.
func singleDisplay(block model.OCRBlock) ([]model.Display, map[int]model.OCRPayload) {
	displays := []model.Display{{ID: 0, Bounds: [4]int{0, 0, 100, 100}}}
	snaps := map[int]model.OCRPayload{
		0: {DisplayID: 0, Resolution: res(100, 100), Blocks: []model.OCRBlock{block}},
	}
	return displays, snaps
}

func TestResolveClickText_BoundingBox(t *testing.T) {
	// Block spans x in [10,40), y in [10,30) (half-open, matching the implementation).
	block := model.OCRBlock{Text: "hello", BBox: [4]int{10, 10, 30, 20}}
	displays, snaps := singleDisplay(block)

	cases := []struct {
		name string
		x, y int
		want *string
	}{
		{"inside center", 20, 15, &block.Text},
		{"inside top-left corner (inclusive)", 10, 10, &block.Text},
		{"inside just before bottom-right", 39, 29, &block.Text},
		{"on right edge (exclusive)", 40, 15, nil},
		{"on bottom edge (exclusive)", 20, 30, nil},
		{"outside block, inside display", 80, 80, nil},
		{"outside all displays", 200, 200, nil},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			got := ResolveClickText(c.x, c.y, displays, snaps)
			assertText(t, got, c.want)
		})
	}
}

func TestResolveClickText_NoSnapshotForDisplay(t *testing.T) {
	displays := []model.Display{{ID: 0, Bounds: [4]int{0, 0, 100, 100}}}
	// Click lands on a real display, but no OCR snapshot exists for it.
	if got := ResolveClickText(50, 50, displays, map[int]model.OCRPayload{}); got != nil {
		t.Fatalf("want nil, got %q", *got)
	}
}

func TestResolveClickText_ResolutionScaling(t *testing.T) {
	// Display bounds are 200x100 but the OCR snapshot is 100x50 → scale 0.5 on both axes.
	displays := []model.Display{{ID: 0, Bounds: [4]int{0, 0, 200, 100}}}
	block := model.OCRBlock{Text: "scaled", BBox: [4]int{40, 20, 20, 10}} // snapshot space: x[40,60) y[20,30)
	snaps := map[int]model.OCRPayload{
		0: {DisplayID: 0, Resolution: res(100, 50), Blocks: []model.OCRBlock{block}},
	}
	// Global click (100,50) → local (50,25) after scaling → inside the block.
	if got := ResolveClickText(100, 50, displays, snaps); got == nil || *got != "scaled" {
		t.Fatalf("want \"scaled\", got %v", got)
	}
	// Global click (200,100) → local (100,50) → outside the block.
	if got := ResolveClickText(200, 100, displays, snaps); got != nil {
		t.Fatalf("want nil, got %q", *got)
	}
}

func TestResolveClickText_SecondaryDisplayOffset(t *testing.T) {
	displays := []model.Display{
		{ID: 0, Bounds: [4]int{0, 0, 100, 100}},
		{ID: 1, Bounds: [4]int{100, 0, 100, 100}}, // to the right of the primary
	}
	snaps := map[int]model.OCRPayload{
		1: {DisplayID: 1, Blocks: []model.OCRBlock{{Text: "second", BBox: [4]int{40, 40, 20, 20}}}},
	}
	// Global (150,50) is on display 1 → local (50,50) (no resolution → bounds, scale 1) → inside.
	if got := ResolveClickText(150, 50, displays, snaps); got == nil || *got != "second" {
		t.Fatalf("want \"second\", got %v", got)
	}
}

func assertText(t *testing.T, got, want *string) {
	t.Helper()
	switch {
	case got == nil && want == nil:
		return
	case got == nil || want == nil:
		t.Fatalf("got %v, want %v", deref(got), deref(want))
	case *got != *want:
		t.Fatalf("got %q, want %q", *got, *want)
	}
}

func deref(s *string) string {
	if s == nil {
		return "<nil>"
	}
	return *s
}
