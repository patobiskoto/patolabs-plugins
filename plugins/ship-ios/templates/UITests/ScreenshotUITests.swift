// ScreenshotUITests.swift — scaffold for `fastlane snapshot`.
// Add this file to your UITest target. If the target uses membershipExceptions
// (Xcode 16 synchronized groups), add it there or it silently won't compile.
//
// Fill in the navigation for each key screen. snapshot() captures at every device
// and locale from the Snapfile automatically — you write the navigation ONCE.

import XCTest

final class ScreenshotUITests: XCTestCase {
    override func setUpWithError() throws {
        continueAfterFailure = false
        let app = XCUIApplication()
        setupSnapshot(app)          // provided by fastlane's SnapshotHelper.swift
        app.launch()
    }

    func testCaptureStoreScreenshots() throws {
        let app = XCUIApplication()

        // 01 — Home / hero screen
        snapshot("01_Home")

        // 02 — the core feature in action (navigate, then capture)
        // app.buttons["startSession"].tap()
        // snapshot("02_Session")

        // 03 — a second differentiating screen
        // app.tabBars.buttons["stats"].tap()
        // snapshot("03_Stats")

        // Add 3–5 screens that tell the app's story. Keep them stable across locales.
    }
}

// NOTE: also add fastlane's SnapshotHelper.swift to the UITest target
// (`fastlane snapshot init` drops it in). It defines setupSnapshot()/snapshot().
