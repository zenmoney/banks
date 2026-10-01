import SwiftUI
import UIKit

private struct NativeManifest: Decodable {
    let schemaVersion: Int
    let pins: [String: String]
    let manifestSha256: String?
    let entries: [NativeEntry]

    static func readFromBundle() throws -> NativeManifest {
        guard let url = Bundle.main.url(forResource: "native-manifest", withExtension: "json") else {
            throw NativeCatalogError.missingManifest
        }
        return try JSONDecoder().decode(NativeManifest.self, from: Data(contentsOf: url))
    }
}

private enum NativeCatalogError: LocalizedError {
    case missingManifest

    var errorDescription: String? {
        switch self {
        case .missingManifest:
            return "native-manifest.json is missing from the app bundle; native catalog unavailable."
        }
    }
}

private struct NativeEntry: Decodable {
    let id: Int?
    let title: String
    let sourcePath: String
    let sourceSha256: String?
    let xmlSha256: String?
    let sourceWidth: String?
    let sourceHeight: String?
    let viewBox: String?
    let naturalWidth: Double?
    let naturalHeight: Double?
    let resourceName: String?
    let error: String?
    let warnings: [String]
    let sourceValidated: Bool?
    let nativeAssetName: String?
    let nativeError: String?

    var identity: String { "\(id.map(String.init) ?? "unknown") | \(sourcePath)" }
    var searchID: String { id.map(String.init) ?? "unknown" }

    // Native availability depends on the original SVG, never on XML conversion.
    var originalImage: NativeImage {
        if let nativeError, !nativeError.isEmpty {
            return .unavailable("Original SVG unavailable: \(nativeError)")
        }
        guard sourceValidated == true else {
            return .unavailable("Original SVG was not validated; native rendering unavailable.")
        }
        guard let name = nativeAssetName, !name.isEmpty else {
            return .unavailable("Original SVG asset name missing from native manifest.")
        }
        guard let image = UIImage(named: name, in: .main, compatibleWith: nil) else {
            return .unavailable("Original SVG asset \(name) missing or could not be loaded by UIImage.")
        }
        guard image.size.width > 0, image.size.height > 0 else {
            return .unavailable("Original SVG asset \(name) loaded without usable image dimensions.")
        }
        return .loaded(image)
    }
}

private enum NativeImage {
    case loaded(UIImage)
    case unavailable(String)
}

struct NativeCatalogView: View {
    @State private var query = ""
    @State private var dark = false
    private let manifest: NativeManifest?
    private let loadError: String?

    init() {
        do {
            manifest = try NativeManifest.readFromBundle()
            loadError = nil
        } catch {
            manifest = nil
            loadError = "Native catalog unavailable: \(error.localizedDescription)"
        }
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    Text("Renderer: UIImage / iOS asset catalog (original SVG)")
                        .font(.subheadline)
                    Toggle("Dark background", isOn: $dark)
                    if let loadError {
                        Text(loadError).foregroundColor(.red)
                    } else if let manifest {
                        Text("Manifest schema \(manifest.schemaVersion) · \(manifest.entries.count) entries")
                            .font(.footnote)
                        LazyVGrid(columns: [GridItem(.adaptive(minimum: 125), spacing: 12)], spacing: 12) {
                            ForEach(filtered(manifest.entries), id: \.identity) { entry in
                                NavigationLink {
                                    NativeDetailView(entry: entry, manifest: manifest, dark: dark)
                                } label: {
                                    NativeTile(entry: entry)
                                }
                                .buttonStyle(.plain)
                            }
                        }
                    }
                }
                .padding()
            }
            .navigationTitle("Original SVG")
            .searchable(text: $query, prompt: "Bank ID or name")
            .background(dark ? Color(red: 0.12, green: 0.13, blue: 0.14) : .white)
        }
        .preferredColorScheme(dark ? .dark : .light)
    }

    private func filtered(_ entries: [NativeEntry]) -> [NativeEntry] {
        let term = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !term.isEmpty else { return entries }
        return entries.filter {
            $0.searchID.localizedCaseInsensitiveContains(term) ||
                $0.title.localizedCaseInsensitiveContains(term)
        }
    }
}

private struct NativeTile: View {
    let entry: NativeEntry

    var body: some View {
        let originalImage = entry.originalImage
        return VStack(spacing: 8) {
            ZStack {
                Rectangle().fill(Color.secondary.opacity(0.12))
                switch originalImage {
                case .loaded(let image):
                    Image(uiImage: image)
                        .resizable()
                        .aspectRatio(contentMode: .fit)
                        .frame(width: 54, height: 54)
                        .accessibilityLabel("Original SVG for \(entry.title)")
                case .unavailable:
                    Image(systemName: "exclamationmark.triangle")
                        .foregroundColor(.red)
                        .accessibilityLabel("Original SVG unavailable")
                }
            }
            .frame(width: 54, height: 54)
            .accessibilityElement(children: .combine)
            Text(entry.title).lineLimit(2)
            Text("ID \(entry.searchID)").font(.caption)
            if case .unavailable(let reason) = originalImage {
                Text(reason).font(.caption2).foregroundColor(.red).lineLimit(3)
            }
        }
        .frame(maxWidth: .infinity, alignment: .top)
        .padding(8)
        .background(Color.secondary.opacity(0.08), in: RoundedRectangle(cornerRadius: 8))
    }
}

private struct NativeDetailView: View {
    let entry: NativeEntry
    let manifest: NativeManifest
    let dark: Bool

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                Text("Renderer: UIImage / iOS asset catalog (original SVG)")
                    .font(.headline)
                Text("ID \(entry.searchID) · \(entry.title)")
                switch entry.originalImage {
                case .loaded(let image):
                    Text("UIImage intrinsic size: \(image.size.width.formatted()) × \(image.size.height.formatted()) pt")
                    Text("UIImage intrinsic size; no SwiftUI fit or crop. Scroll both axes if needed.")
                        .font(.footnote)
                    ScrollView([.horizontal, .vertical]) {
                        Image(uiImage: image)
                            .fixedSize(horizontal: true, vertical: true)
                            .frame(width: image.size.width, height: image.size.height)
                            .accessibilityLabel("Original SVG for \(entry.title) at native size")
                    }
                    .frame(maxWidth: .infinity, minHeight: min(image.size.height, 350), maxHeight: 350)
                    .background(dark ? Color(red: 0.12, green: 0.13, blue: 0.14) : .white)
                    .border(Color.secondary)
                case .unavailable(let reason):
                    Text(reason).foregroundColor(.red)
                }
                Group {
                    metadata("Source", entry.sourcePath)
                    metadata("SVG width", entry.sourceWidth)
                    metadata("SVG height", entry.sourceHeight)
                    metadata("SVG viewBox", entry.viewBox)
                    metadata("Source natural dimensions", naturalDimensions)
                    metadata("Original SVG SHA-256", entry.sourceSha256)
                    metadata("Derived XML SHA-256", entry.xmlSha256)
                    metadata("Derived XML resource", entry.resourceName)
                }
                Group {
                    metadata("Native asset", entry.nativeAssetName)
                    metadata("Manifest SHA-256", manifest.manifestSha256)
                    metadata("Pins", manifest.pins.sorted { $0.key < $1.key }
                        .map { "\($0.key)=\($0.value)" }.joined(separator: ", "))
                }
                if let error = entry.error {
                    Text("XML conversion error (independent of original SVG): \(error)")
                        .foregroundColor(.orange)
                }
                ForEach(entry.warnings, id: \.self) { warning in
                    Text("XML conversion warning: \(warning)")
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding()
        }
        .background(dark ? Color(red: 0.12, green: 0.13, blue: 0.14) : .white)
        .navigationTitle(entry.title)
        .navigationBarTitleDisplayMode(.inline)
    }

    private var naturalDimensions: String? {
        guard let width = entry.naturalWidth, let height = entry.naturalHeight else { return nil }
        return "\(width) × \(height)"
    }

    private func metadata(_ label: String, _ value: String?) -> some View {
        Text("\(label): \(value ?? "not available")")
            .font(.footnote)
            .textSelection(.enabled)
    }
}
