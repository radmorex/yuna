// Распознавание текста на изображении встроенным в macOS движком Vision — локально, без сети.
// Вывод: по строке на распознанную строку, сверху вниз. Код возврата 2 — файл не читается как изображение.
import Foundation
import Vision

guard CommandLine.arguments.count == 2 else { exit(64) }
let url = URL(fileURLWithPath: CommandLine.arguments[1])
let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.usesLanguageCorrection = true
request.recognitionLanguages = ["ru-RU", "uk-UA", "en-US"]
if #available(macOS 13.0, *) { request.automaticallyDetectsLanguage = true }
do {
    try VNImageRequestHandler(url: url, options: [:]).perform([request])
} catch {
    FileHandle.standardError.write("не удалось прочитать изображение: \(error.localizedDescription)\n".data(using: .utf8)!)
    exit(2)
}
for obs in request.results ?? [] {
    if let best = obs.topCandidates(1).first { print(best.string) }
}
