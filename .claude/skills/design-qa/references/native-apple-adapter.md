# Native Apple adapter: Xcode Simulator / XCUITest evidence for Design QA

## Evidence status

Grounded in a real, current pattern found in `pet-life-simulator`
(read-only reference workspace at
`${HOME}/Bryant-Developments/horonomy-work/pet-life-simulator`, per
`swift-development`'s own HORO-976 findings — this skill does not modify
that repo). The specific evidence: in `apps/ios/PetLifeSimulatorUITests/`,
the file `ArtProofCaptureSupport.swift`, committed on branch
`visual-mvp-1/HORO-878/art_pipeline` (commit `0142514`, "Record actual
accessibility preferences beside captures"), consumed by
`ArtPipelineProofUITests.swift` in that same directory. This is a
real, committed pattern in a real Horonom iOS repo, not a fabricated
example — file/target names below are copied verbatim from it. It has not
been run as part of this ticket's own validation pass (this skill only
inspected the source); see `SKILL.md`'s composition note and HORO-983 for
actual dogfooding scope. The base app/UI-test targets
(`PetLifeSimulatorTests`, `PetLifeSimulatorUITests`,
`PetLifeSimulatorOwnershipJourneyUITests.swift`, `CareUITests.swift`) exist
on the repo's main line and are also documented as real in
`agents/skills/swift-development/examples/
targeted-swiftpm-test-then-full-build.md`.

## The screenshot-capture pattern

```swift
import UIKit
import XCTest

/// Screen-level capture includes the actual SpriteKit/Metal composition.
@MainActor
enum ArtProofCaptureSupport {
  static func capture(_ name: String, in test: XCTestCase) {
    let attachment = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
    attachment.name = name
    attachment.lifetime = .keepAlways
    test.add(attachment)
    let preferences = XCTAttachment(
      string:
        "reduceMotion=\(UIAccessibility.isReduceMotionEnabled); "
        + "darkerColors=\(UIAccessibility.isDarkerSystemColorsEnabled); "
        + "contentSize=\(UIApplication.shared.preferredContentSizeCategory.rawValue)")
    preferences.name = name + "-system-accessibility"
    preferences.lifetime = .keepAlways
    test.add(preferences)
  }
}
```

Two things worth calling out about this real pattern:

- `XCUIScreen.main.screenshot()` captures the whole physical screen,
  including SpriteKit/Metal-rendered content that a view-hierarchy-only
  capture (`XCUIElement.screenshot()`) would miss — the comment on the enum
  says exactly why the repo chose screen-level over element-level capture.
- It attaches a **second** text attachment recording the actual system
  accessibility state (`isReduceMotionEnabled`,
  `isDarkerSystemColorsEnabled`, `preferredContentSizeCategory`) alongside
  every screenshot — so a reviewer (human or agent) never has to guess what
  conditions a given capture was taken under. This directly serves
  dimensions 4/5 in `design-qa-dimensions.md` (accessibility, motion): the
  evidence is self-describing.

Usage, from `ArtPipelineProofUITests.swift`:

```swift
let app = XCUIApplication()
app.launchArguments = [
  "-ResetPersistenceForTesting", "-ArtPipelineProof", "-HomeSceneForceIntent", "idle",
]
app.launch()
XCTAssertTrue(app.buttons["Journal"].waitForExistence(timeout: 15))
ArtProofCaptureSupport.capture("art-proof-idle", in: self)
```

Generalize the shape, not the literal launch arguments: launch the app into
a **deterministic** state via real launch arguments/environment (this repo
uses `-ResetPersistenceForTesting` plus scenario-specific flags like
`-HomeSceneForceIntent`), wait for a real readiness signal
(`waitForExistence`, not a sleep), then capture. A product with no such
deterministic-state launch hook needs one added before Design QA evidence
can be trusted — a screenshot of whatever state the app happened to boot
into is not comparable evidence.

## Multi-state / multi-device coverage

- Drive through the same states dimension-by-dimension: idle/alert/sleep-
  style app states in the example above map to "responsive/input-mode" and
  "motion/interaction feedback" coverage — capture each named state
  explicitly rather than one arbitrary screenshot.
- For device/size-class coverage, run the same `XCUIApplication` launch/
  capture sequence across multiple already-installed simulator
  destinations (`xcrun simctl list devices available`, per
  `swift-development/references/xcode-swiftpm-detection.md` §4) — different
  screen sizes, not just different app states.
- Reduced-motion / larger-text conditions are set via the Simulator's own
  Settings app or `xcrun simctl` accessibility toggles before launch, then
  confirmed in the capture's own accessibility-preferences attachment
  (per the pattern above) rather than assumed.

## Why this is never a Playwright substitute, and vice versa

`XCUIApplication` drives the real `UIKit`/`SwiftUI`/`SpriteKit` rendering
and accessibility stack on a real (simulated) OS — a web automation tool
cannot exercise any of that, and native tooling cannot exercise a web
product's DOM/CSS/browser-engine behavior. `SKILL.md`'s "When NOT to use"
rule (never force Playwright onto a native product, and the reverse) is
not a style preference — the two tools observe genuinely different render
pipelines.

## What this adapter does not prove

Everything `swift-development/SKILL.md`'s "When NOT to use" section
already states applies to Design QA evidence too: simulator-only capture is
not device/provisioning proof, and a passing UI test target is not proof of
real-device sensor/notification/background behavior. A Design QA verdict
built entirely on simulator captures should say so explicitly, not imply
device-equivalent confidence.
