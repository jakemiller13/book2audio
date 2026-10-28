// OCR helper for book2audio. Uses Apple's Vision framework (on-device, free).
//
// Usage:
//   ocr image1.jpg image2.png ...        -> one JSON record per image
//   ocr --video clip.mov [--every 0.5] [--shard k/N]   -> one JSON record per sampled frame
//
// Output: JSON lines on stdout, each {"source", "time", "width", "height", "pass", "lines": [...]}
// where each line is {"text", "conf", "x0", "y0", "x1", "y1", "lx", "ly", "rx", "ry"} in normalized coords,
// origin at TOP-left (converted from Vision's bottom-left).

import AVFoundation
import CoreImage
import Foundation
import ImageIO
import Vision

struct Line: Codable {
    let text: String; let conf: Float
    let x0, y0, x1, y1: Double          // axis-aligned bounding box
    let lx, ly, rx, ry: Double          // midpoints of the line's left and right ends (handles tilt)
}
struct Record: Codable {
    let source: String; let time: Double?; let width, height: Int
    let pass: String                     // "full", "left"/"right" (one-page crop) or "fast"
    var crop: [Double]? = nil            // x range of the crop, for "left"/"right"
    let lines: [Line]
}

// x position of the book's spine: the vertical strip crossed by the fewest text lines,
// with real text on both sides (so the empty desk beside the book doesn't count).
// A line only "crosses" if it reaches 1% past the strip on both sides: lines curling
// into the spine often overshoot it slightly. Returns nil for a single page.
// Keep in sync with find_gutter() in book2audio.py.
func findGutter(_ lines: [Line]) -> Double? {
    let long = lines.filter { $0.text.count > 15 }
    guard long.count >= 6 else { return nil }
    var counts: [(x: Double, n: Int)] = []
    for i in 20...80 {
        let x = Double(i) / 100
        let left = long.filter { ($0.x0 + $0.x1) / 2 < x }.count
        if left < 3 || long.count - left < 3 { continue }
        counts.append((x, lines.filter { $0.x0 < x - 0.01 && x + 0.01 < $0.x1 }.count))
    }
    guard let minN = counts.map({ $0.n }).min(), minN <= max(1, lines.count / 20) else { return nil }
    let xs = counts.filter { $0.n == minN }.map { $0.x }
    return xs[xs.count / 2]
}

func recognize(_ image: CGImage, orientation: CGImagePropertyOrientation,
               level: VNRequestTextRecognitionLevel = .accurate) -> [Line] {
    let req = VNRecognizeTextRequest()
    req.recognitionLevel = level
    req.usesLanguageCorrection = true
    req.recognitionLanguages = ["en-US"]
    let handler = VNImageRequestHandler(cgImage: image, orientation: orientation, options: [:])
    try? handler.perform([req])
    return (req.results ?? []).compactMap { obs in
        guard let c = obs.topCandidates(1).first else { return nil }
        let b = obs.boundingBox
        return Line(text: c.string, conf: c.confidence,
                    x0: b.minX, y0: 1 - b.maxY, x1: b.maxX, y1: 1 - b.minY,
                    lx: (obs.topLeft.x + obs.bottomLeft.x) / 2, ly: 1 - (obs.topLeft.y + obs.bottomLeft.y) / 2,
                    rx: (obs.topRight.x + obs.bottomRight.x) / 2, ry: 1 - (obs.topRight.y + obs.bottomRight.y) / 2)
    }
}

// Curved two-page spreads: Vision sometimes skips lines near the spine on the full image
// but finds them when given one page at a time, so we OCR the full frame and each page
// (split at the spine, which isn't necessarily mid-frame in video).
// Half-crop coordinates are mapped back into full-image space.
func recognizeAllPasses(_ image: CGImage, orientation: CGImagePropertyOrientation,
                        source: String, time: Double?) -> [Record] {
    var upright = image
    if orientation != .up, let rotated = rotate(image, orientation) { upright = rotated }
    let w = upright.width, h = upright.height
    let full = recognize(upright, orientation: .up)
    var out = [Record(source: source, time: time, width: w, height: h, pass: "full", lines: full)]
    let halves = findGutter(full).map { g in [("left", 0.0, min(1, g + 0.06)), ("right", max(0, g - 0.06), 1.0)] } ?? []
    for (name, a, b) in halves {
        let rect = CGRect(x: Double(w) * a, y: 0, width: Double(w) * (b - a), height: Double(h))
        guard let crop = upright.cropping(to: rect.integral) else { continue }
        let s = b - a
        let lines = recognize(crop, orientation: .up).map { l in
            Line(text: l.text, conf: l.conf, x0: a + l.x0 * s, y0: l.y0, x1: a + l.x1 * s, y1: l.y1,
                 lx: a + l.lx * s, ly: l.ly, rx: a + l.rx * s, ry: l.ry)
        }
        out.append(Record(source: source, time: time, width: w, height: h, pass: name, crop: [a, b], lines: lines))
    }
    // The fast recognizer occasionally finds short lines (a lone "end.") that the accurate one skips.
    out.append(Record(source: source, time: time, width: w, height: h, pass: "fast",
                      lines: recognize(upright, orientation: .up, level: .fast)))
    return out
}

func rotate(_ image: CGImage, _ orientation: CGImagePropertyOrientation) -> CGImage? {
    let ci = CIImage(cgImage: image).oriented(orientation)
    return CIContext().createCGImage(ci, from: ci.extent)
}

func emit(_ r: Record) {
    let data = try! JSONEncoder().encode(r)
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write("\n".data(using: .utf8)!)
}

func ocrImage(_ path: String) {
    let url = URL(fileURLWithPath: path) as CFURL
    guard let src = CGImageSourceCreateWithURL(url, nil),
          let img = CGImageSourceCreateImageAtIndex(src, 0, nil) else {
        FileHandle.standardError.write("cannot read \(path)\n".data(using: .utf8)!)
        return
    }
    let props = CGImageSourceCopyPropertiesAtIndex(src, 0, nil) as? [CFString: Any]
    let raw = (props?[kCGImagePropertyOrientation] as? UInt32) ?? 1
    let orient = CGImagePropertyOrientation(rawValue: raw) ?? .up
    recognizeAllPasses(img, orientation: orient, source: path, time: nil).forEach(emit)
}

// Vision serializes work within one process, so callers parallelize by running several
// processes, each taking every Nth frame (--shard k/N).
func ocrVideo(_ path: String, every: Double, shard: Int, of shards: Int) {
    let asset = AVURLAsset(url: URL(fileURLWithPath: path))
    let gen = AVAssetImageGenerator(asset: asset)
    gen.appliesPreferredTrackTransform = true  // handles portrait/landscape phone video
    gen.requestedTimeToleranceBefore = .zero
    gen.requestedTimeToleranceAfter = .zero
    let duration = CMTimeGetSeconds(asset.duration)
    var i = shard
    while Double(i) * every < duration {
        let t = Double(i) * every
        if let img = try? gen.copyCGImage(at: CMTime(seconds: t, preferredTimescale: 600), actualTime: nil) {
            recognizeAllPasses(img, orientation: .up, source: path, time: t).forEach(emit)
        }
        i += shards
    }
}

var args = Array(CommandLine.arguments.dropFirst())
func option(_ name: String) -> String? {
    guard let i = args.firstIndex(of: name), i + 1 < args.count else { return nil }
    let v = args[i + 1]
    args.removeSubrange(i...(i + 1))
    return v
}
if args.first == "--video" {
    args.removeFirst()
    let every = option("--every").flatMap(Double.init) ?? 0.5
    let shard = (option("--shard") ?? "0/1").split(separator: "/").compactMap { Int($0) }
    for p in args { ocrVideo(p, every: every, shard: shard[0], of: max(1, shard[1])) }
} else {
    for p in args { ocrImage(p) }
}
