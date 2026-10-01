import SwiftUI
import UIKit
import IconCatalog

@main
struct IconCatalogApp: App {
    var body: some Scene {
        WindowGroup {
            TabView {
                ComposeCatalogController()
                    .tabItem { Label("Compose XML", systemImage: "square.grid.2x2") }
                NativeCatalogView()
                    .tabItem { Label("Original SVG", systemImage: "photo") }
            }
        }
    }
}

private struct ComposeCatalogController: UIViewControllerRepresentable {
    func makeUIViewController(context: Context) -> UIViewController {
        MainViewControllerKt.MainViewController()
    }

    func updateUIViewController(_ controller: UIViewController, context: Context) {}
}
