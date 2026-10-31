// Захват системного звука через ScreenCaptureKit. В stdout: сырой PCM s16le, 16 кГц, моно.
// На диск ничего не пишет. Нужно разрешение «Запись экрана» в настройках macOS.
import AVFoundation
import Foundation
import ScreenCaptureKit

final class Output: NSObject, SCStreamOutput, SCStreamDelegate {
    func stream(_ stream: SCStream, didOutputSampleBuffer buffer: CMSampleBuffer, of type: SCStreamOutputType) {
        guard type == .audio, let block = CMSampleBufferGetDataBuffer(buffer) else { return }
        var length = 0
        var pointer: UnsafeMutablePointer<CChar>?
        guard CMBlockBufferGetDataPointer(block, atOffset: 0, lengthAtOffsetOut: nil, totalLengthOut: &length, dataPointerOut: &pointer) == kCMBlockBufferNoErr,
              let raw = pointer else { return }
        let count = length / MemoryLayout<Float32>.size
        var pcm = [Int16](repeating: 0, count: count)
        raw.withMemoryRebound(to: Float32.self, capacity: count) { samples in
            for i in 0..<count {
                pcm[i] = Int16(max(-1, min(1, samples[i])) * 32767)
            }
        }
        pcm.withUnsafeBytes { FileHandle.standardOutput.write(Data($0)) }
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        FileHandle.standardError.write(Data("capture stopped: \(error.localizedDescription)\n".utf8))
        exit(3)
    }
}

let output = Output()
var active: SCStream?

Task {
    do {
        let content = try await SCShareableContent.excludingDesktopWindows(true, onScreenWindowsOnly: false)
        guard let display = content.displays.first else {
            FileHandle.standardError.write(Data("no display\n".utf8))
            exit(2)
        }
        let filter = SCContentFilter(display: display, excludingApplications: [], exceptingWindows: [])
        let config = SCStreamConfiguration()
        config.capturesAudio = true
        config.sampleRate = 16_000
        config.channelCount = 1
        config.excludesCurrentProcessAudio = true
        // Экран не нужен, но поток видео обязателен: самый маленький и редкий.
        config.width = 2
        config.height = 2
        config.minimumFrameInterval = CMTime(value: 1, timescale: 1)
        let stream = SCStream(filter: filter, configuration: config, delegate: output)
        try stream.addStreamOutput(output, type: .audio, sampleHandlerQueue: DispatchQueue(label: "audio"))
        try await stream.startCapture()
        active = stream
    } catch {
        FileHandle.standardError.write(Data("permission or start error: \(error.localizedDescription)\n".utf8))
        exit(1)
    }
}

signal(SIGPIPE, SIG_DFL)
RunLoop.main.run()
