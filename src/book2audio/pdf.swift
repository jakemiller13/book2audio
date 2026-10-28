// PDF helper for book2audio. Uses Apple's PDFKit (on-device, free).
//
// Usage:
//   pdfpages paper.pdf OUTDIR [--dpi 140]
//
// Writes OUTDIR/pNNN.png (an image of each page) and prints JSON lines on stdout:
// first {"title", "author", "pages"}, then one record per page:
// {"page", "width", "height", "image", "text", "lines": [{"text", "x0", "y0", "x1", "y1"}]}
// where "text" is the page's embedded text layer (empty for a scanned page) and line
// coordinates are fractions of the page, origin at the TOP-left.

import AppKit
import Foundation
import PDFKit

func fail(_ msg: String) -> Never {
    FileHandle.standardError.write((msg + "\n").data(using: .utf8)!)
    exit(1)
}

func emit(_ obj: [String: Any]) {
    let data = try! JSONSerialization.data(withJSONObject: obj)
    print(String(data: data, encoding: .utf8)!)
}

var args = Array(CommandLine.arguments.dropFirst())
var dpi = 140.0
if let i = args.firstIndex(of: "--dpi"), i + 1 < args.count {
    dpi = Double(args[i + 1]) ?? dpi
    args.removeSubrange(i...(i + 1))
}
guard args.count == 2 else { fail("usage: pdfpages paper.pdf OUTDIR [--dpi 140]") }
guard let doc = PDFDocument(url: URL(fileURLWithPath: args[0])) else { fail("Can't open \(args[0]) as a PDF") }
if doc.isLocked { fail("\(args[0]) is password-protected") }
let outDir = URL(fileURLWithPath: args[1])
try? FileManager.default.createDirectory(at: outDir, withIntermediateDirectories: true)

let attrs = doc.documentAttributes ?? [:]
emit(["title": attrs[PDFDocumentAttribute.titleAttribute] as? String ?? "",
      "author": attrs[PDFDocumentAttribute.authorAttribute] as? String ?? "",
      "pages": doc.pageCount])

for i in 0..<doc.pageCount {
    guard let page = doc.page(at: i) else { continue }
    let box = page.bounds(for: .mediaBox)
    var lines: [[String: Any]] = []
    for sel in page.selection(for: box)?.selectionsByLine() ?? [] {
        let text = (sel.string ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        if text.isEmpty { continue }
        let b = sel.bounds(for: page)
        lines.append(["text": text,
                      "x0": (b.minX - box.minX) / box.width, "x1": (b.maxX - box.minX) / box.width,
                      "y0": 1 - (b.maxY - box.minY) / box.height, "y1": 1 - (b.minY - box.minY) / box.height])
    }

    let name = String(format: "p%03d.png", i + 1)
    let scale = dpi / 72.0
    let img = page.thumbnail(of: NSSize(width: box.width * scale, height: box.height * scale), for: .mediaBox)
    guard let tiff = img.tiffRepresentation, let rep = NSBitmapImageRep(data: tiff),
          let png = rep.representation(using: .png, properties: [:]) else { fail("Couldn't render page \(i + 1)") }
    do { try png.write(to: outDir.appendingPathComponent(name)) } catch { fail("Couldn't write \(name): \(error)") }

    emit(["page": i + 1, "width": box.width, "height": box.height, "image": name,
          "text": page.string ?? "", "lines": lines])
}
