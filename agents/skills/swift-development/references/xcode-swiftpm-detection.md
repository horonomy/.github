# Xcode / SwiftPM detection, scoping, and validation boundary

## 1. Detecting project shape

Check, in order, before choosing a command family:

1. `find . -maxdepth 4 -iname "Package.swift"` — one hit per SwiftPM
   package root. A repo may have several (e.g. `PetDomain`,
   `PetPersistence`, `PetSimulation`, `PetNotifications` each with their
   own `Package.swift` in a `packages/` directory is a real, current shape
   — see the worked example).
2. `find . -maxdepth 4 -iname "*.xcodeproj" -o -iname "*.xcworkspace"` —
   an Xcode project/workspace root. Prefer `.xcworkspace` over
   `.xcodeproj` when both exist at the same level (a workspace usually
   means CocoaPods/multiple-project composition).
3. A repo with both is the common umbrella-app shape: local SwiftPM
   packages under a `packages/`-style directory, resolved as local
   dependencies into an `.xcodeproj` under an `apps/`-style directory.
   `xcodebuild -list -project <path>` prints "Resolved source packages"
   for each local package plus the project's own targets — use it to
   confirm the wiring before assuming which command family applies.

Never assume "one repo = one shape" — a monorepo may have unrelated
packages that don't feed the app at all; only the packages actually
resolved into the touched app/workspace are in scope for a Full Gate on
that app.

## 2. Scheme/target selection

- `xcodebuild -list -project <path>.xcodeproj` (or `-workspace` for a
  `.xcworkspace`) lists every scheme and target with no build performed —
  always run this before guessing a scheme name from the directory name.
- A scheme name does not always match its primary target name exactly;
  confirm from the `-list` output, not by assumption.
- For a pure SwiftPM package, `swift package describe --type json` lists
  every product/target/test-target name without building.

## 3. Targeted tests vs. full scopes

**SwiftPM:**
- Narrow: `swift test --filter <TargetTests>.<TestCase>/<testMethod>` (or
  `--filter <TargetTests>` to scope to one test target) — cheapest signal
  for a single package's change.
- Full Gate for that package: `swift test` with no filter.
- `swift build` alone only proves compilation, not test correctness — it
  is a legitimate fast Narrow step (e.g. confirming a type-level change
  compiles) but never a substitute for running the tests that exercise the
  changed behavior.
- Plain `swift test` runs on the host toolchain and only works for a
  package with no iOS-only APIs (UIKit, iOS-only SwiftUI, etc.). A package
  that imports one will fail to build under `swift test` even though its
  logic is otherwise sound. Local SwiftPM packages consumed by an Xcode
  project are typically also exposed as their own Xcode schemes
  (`xcodebuild -list` on the umbrella project lists them alongside the
  app's own scheme) — for such a package, fall back to `xcodebuild test
  -scheme <PackageScheme> -destination 'platform=iOS
  Simulator,name=<device>'` instead of plain `swift test`.

**Xcode:**
- Narrow: `xcodebuild test -scheme <Scheme> -destination <dest>
  -only-testing:<TestTarget>/<TestClass>/<testMethod>` — restricts
  discovery and execution to the one test that exercises the change,
  avoiding a full-suite relaunch cost for an unrelated edit.
- Full Gate: `xcodebuild build` (or `test` with no `-only-testing`) against
  the app's real scheme — the authoritative signal that the app target
  still links and its full test suite passes.
- `-skip-testing:` is the inverse tool (exclude specific known-slow/
  irrelevant tests) — use it deliberately, never as a way to silently
  drop a test that should be covering the change.

## 4. Simulator selection without unnecessary churn

- `xcrun simctl list devices available` enumerates already-installed
  simulator runtimes/devices — always check this before specifying a
  `-destination`, so a task never accidentally triggers a new runtime
  download (e.g. requesting an iOS version with no installed runtime).
- Prefer an already-booted or previously-used simulator over a fresh
  device allocation when either satisfies the task — a new device does
  not exercise anything a reused one wouldn't.
- If no installed runtime satisfies a genuine requirement (e.g. a
  platform-version-specific bug), that is itself a case for surfacing the
  gap rather than silently downloading a new component — component
  downloads can be large and are a deliberate resource decision, not a
  default background action.

## 5. App vs. package vs. framework validation boundary

- A SwiftPM library/framework package's own test suite validates that
  package's logic in isolation — it says nothing about how the consuming
  app wires, presents, or drives it at runtime.
- An app target's unit tests (e.g. an `XCTest`/Swift Testing target
  bundled with the app) validate app-level logic, still without a real
  UI/interaction pass.
- An app target's UI test target is the only one of the three that
  exercises real user-facing interaction — and even that is simulator-bound
  unless explicitly run against a physical device. The actual requirement
  is native UI-interaction evidence for an Apple-native surface;
  `XCUITest` is today's implementation of that requirement (§6 of
  `governance/engineering/agent-skill-architecture.md`), not the
  requirement itself.
- Passing all three together is still not equivalent to real
  device/provisioning validation (see the `SKILL.md` "When NOT to use"
  section) — device-specific behavior (real sensors, real push
  notification delivery, real background execution limits, real memory
  pressure) is outside what any of the three prove.

## 6. When a signing/provisioning-sensitive operation requires a human

Stop and ask rather than proceeding autonomously whenever an operation
would:

- Create, rotate, or select a signing certificate or provisioning
  profile.
- Change a project's development team, bundle identifier, or code-signing
  entitlements.
- Switch a target between automatic and manual signing.
- Attempt an archive/export step that requires a distribution
  certificate.

A build or test failure that surfaces as a signing/provisioning error is
diagnostic signal, not an invitation to change identity configuration to
make the red go green — report the failure and the exact identity
configuration observed, and let a human decide.
