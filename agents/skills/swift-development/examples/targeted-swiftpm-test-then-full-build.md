# Worked example: targeted SwiftPM test, then the umbrella Xcode Full Gate

Grounded in a real, current repo found during HORO-976 (read-only
reference — this skill does not modify that repo): `pet-life-simulator`,
an umbrella-app shape with several local SwiftPM packages
(`packages/PetDomain`, `packages/PetPersistence`, `packages/PetSimulation`,
`packages/PetNotifications`) consumed by an iOS app project
(`apps/ios/PetLifeSimulator.xcodeproj`, scheme `PetLifeSimulator`, with
`PetLifeSimulatorTests` and `PetLifeSimulatorUITests` targets). Names and
shapes below reflect that repo's actual layout at inspection time; adapt
the same procedure to whatever package/target names the touched repo
actually has.

## Scenario

A change touches `PetDomain`'s `NeedLevel` type (e.g.
`packages/PetDomain/Sources/PetDomain/NeedLevel.swift`), which has its own
test target (`PetDomainTests`, including `NeedLevelTests.swift`) and is
also consumed by the `PetLifeSimulator` app target.

## 1. Explore

Confirm the package's shape before running anything:

```
swift package describe --type json --package-path packages/PetDomain
```

This lists `PetDomain`'s products/targets/test-targets (`PetDomain`,
`PetDomainTests`) without building.

## 2. Narrow — scoped SwiftPM test first

Run only the test target that exercises the changed type — not the whole
package, and not the app:

```
swift test --package-path packages/PetDomain --filter PetDomainTests.NeedLevelTests
```

This is the cheapest signal for a change confined to one type in one
package: it proves or disproves the specific behavior without paying for
the other package test targets (`ExpenseLedgerTests`, `PetStateTests`,
etc.) or an app build.

## 3. Validate / Escalate

If the targeted test fails unexpectedly, escalate through the L0–L3
diagnostic ladder (`engineering-loop`'s
`references/diagnostic-contract.md`) — e.g. re-run with `--verbose` for
file/line, then inspect the actual assertion failure, before assuming the
production code (rather than the test) is wrong.

Do not treat this package-level green as proof the app works — `NeedLevel`
feeds `PetState`/`HouseholdState`, which the `PetLifeSimulator` app target
actually renders; a package-level pass says nothing about the app's
`HomeScene`/`CareView` wiring.

## 4. Full Gate — the umbrella app

Before declaring the change done, confirm the consuming app target still
links and its own test suites still pass. First confirm the actual scheme
and available destinations without guessing:

```
xcodebuild -list -project apps/ios/PetLifeSimulator.xcodeproj
xcrun simctl list devices available
```

Then run the app's real test target(s) against an already-installed
simulator (no new runtime download):

```
xcodebuild test \
  -project apps/ios/PetLifeSimulator.xcodeproj \
  -scheme PetLifeSimulator \
  -destination 'platform=iOS Simulator,name=<an already-installed device>' \
  -only-testing:PetLifeSimulatorTests
```

For the actual Full Gate (not just a narrow check), drop `-only-testing`
so the complete `PetLifeSimulatorTests` suite runs, and only add
`PetLifeSimulatorUITests` when the change plausibly affects rendered
UI/interaction — a UI test run is simulator-bound and, per `SKILL.md`, is
still not device/provisioning proof even when green.

## What this example does not claim

A green `PetDomainTests` run plus a green `PetLifeSimulatorTests`/
`PetLifeSimulatorUITests` run on a simulator is strong package- and
app-level evidence — it is not evidence the app behaves correctly on a
real device, under real notification delivery, or under a real signing/
provisioning configuration. If the actual task requires that proof, that
is a separate, explicitly-scoped validation step (see `SKILL.md`'s "When
NOT to use" and the signing/provisioning escalation rule), not something
this example's commands establish.
