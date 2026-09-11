<!-- horonom:generated -->
<!-- Source: horonomy/.github agents/skills/swift-development/SKILL.md. Do not hand-edit — rerun `python3 agents/common/project_skills.py`. -->

# SKILL.md — swift-development

## Purpose

Xcode/SwiftPM targeted iteration and iOS validation for the company-common
capability referenced by `governance/engineering/agent-skill-architecture.md`
(HORO-969) §1. This skill owns the actual `swift build`/`swift test`/
`xcodebuild` commands and Apple-native validation boundary; it composes
with `engineering-loop`, which owns the execution loop and diagnostic
discipline around them.

## Type

Auto-used. Invoke whenever iterating on a Swift package or Xcode project —
`manifest.yaml` declares `stacks: [swift]`, so applicability activates on
repository evidence: `Package.swift`, `.xcodeproj`, or `.xcworkspace`
(`governance/engineering/agent-skill-architecture.md` §3).

## When to use

- Any Swift package (pure SwiftPM library/framework) or Xcode-based iOS/
  macOS app in a repo that has adopted this skill.
- Iterating on Swift source, package tests, app unit/UI tests, or a build
  configuration change.

## When NOT to use

- As proof the shipped product actually works. **Package/unit tests passing
  is not proof the iOS app launches or behaves correctly** — a `swift test`
  green run proves the tested package's logic in isolation, not the app
  target's wiring or real-device behavior (`product-validation` owns
  product-level QA; `design-qa` owns rendered-UI verification).
- As proof the app is deployable. **Simulator-only validation is not
  device/provisioning proof** — it exercises no real signing/provisioning
  chain and never substitutes for real-device or archive/export validation.
- To make automation pass by changing product identity. **Never mutate
  signing, team, bundle identifier, or provisioning profile configuration
  merely to make a build or CI check succeed** — see the escalation rule
  below.

## Routing: detect project shape first

Before running anything, determine whether the touched surface is a pure
SwiftPM package, an Xcode project/workspace, or both (a common shape: one
or more SwiftPM packages consumed by an umbrella `.xcodeproj`). Full
detection procedure, scheme/target selection, and simulator-selection
guidance: `references/xcode-swiftpm-detection.md`.

- **Pure SwiftPM package** → prefer `swift build`/`swift test`, scoped with
  `--filter` to the touched target for the Narrow stage (engineering-loop
  §"Narrow"), full `swift test` for the Full Gate.
- **Xcode project/workspace** → resolve the correct scheme, prefer a
  targeted `xcodebuild test -only-testing:<TestTarget>/<TestClass>` for
  Narrow, a full `xcodebuild build`/`test` for the Full Gate.
- **Both together** → validate the SwiftPM package(s) directly first
  (fastest signal), then the umbrella app build/tests — a package-level
  pass does not imply the app target that consumes it still links and
  runs.

Pick an already-installed simulator runtime/device for any `-destination`;
never trigger a new simulator or Xcode component download that a task
doesn't genuinely require.

## Signing/provisioning escalation

Any operation that would create, change, or requires reading a signing
identity, team, provisioning profile, or entitlement to proceed is a stop
point, not an autonomy point — surface it and ask for a human decision
rather than working around it (e.g. by switching to automatic signing,
downgrading to a personal team, or disabling code signing to force a
build through).

## Worked examples

- `examples/targeted-swiftpm-test-then-full-build.md` — scoping a SwiftPM
  package test run before the umbrella Xcode Full Gate.

## References

- `references/xcode-swiftpm-detection.md` — project-shape detection,
  scheme/target selection, targeted test scoping, simulator selection, and
  the app/package/framework validation boundary in full.
