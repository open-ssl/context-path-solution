package geometry

import "contextpath/streamer/internal/model"

// displayForPoint returns the first display whose bounds contain (x, y), half-open on
// the right/bottom edges — matching the Python implementation exactly.
func displayForPoint(x, y int, displays []model.Display) (model.Display, bool) {
	for _, d := range displays {
		bx, by, bw, bh := d.Bounds[0], d.Bounds[1], d.Bounds[2], d.Bounds[3]
		if bx <= x && x < bx+bw && by <= y && y < by+bh {
			return d, true
		}
	}
	return model.Display{}, false
}

// ResolveClickText maps a global click to the OCR text block under it: find the display,
// scale the point into the snapshot's pixel space, then find the containing block.
func ResolveClickText(x, y int, displays []model.Display, snapshots map[int]model.OCRPayload) *string {
	d, ok := displayForPoint(x, y, displays)
	if !ok {
		return nil
	}
	snap, ok := snapshots[d.ID]
	if !ok {
		return nil
	}

	bx, by, bw, bh := d.Bounds[0], d.Bounds[1], d.Bounds[2], d.Bounds[3]
	resW, resH := bw, bh
	if snap.Resolution != nil {
		resW, resH = snap.Resolution[0], snap.Resolution[1]
	}
	scaleX, scaleY := 1.0, 1.0
	if bw != 0 {
		scaleX = float64(resW) / float64(bw)
	}
	if bh != 0 {
		scaleY = float64(resH) / float64(bh)
	}
	localX := float64(x-bx) * scaleX
	localY := float64(y-by) * scaleY

	for _, b := range snap.Blocks {
		ox, oy, ow, oh := float64(b.BBox[0]), float64(b.BBox[1]), float64(b.BBox[2]), float64(b.BBox[3])
		if ox <= localX && localX < ox+ow && oy <= localY && localY < oy+oh {
			t := b.Text
			return &t
		}
	}
	return nil
}
