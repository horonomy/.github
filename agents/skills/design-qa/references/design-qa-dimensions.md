# The seven Design QA dimensions — operationalized

Each dimension below states what to actually look at and check, not just
the ticket's one-line label. These are judgment checks performed against
gathered evidence (screenshots, DOM/accessibility-tree snapshots, console/
network logs, interaction recordings) — see `web-playwright-adapter.md` and
`native-apple-adapter.md` for how the evidence is gathered per platform.

## 1. First-impression / comprehension

The 5-second-comprehension heuristic, applied to any screen a user lands on
cold (a marketing homepage, an app's first launch screen, an empty state):
look at the rendered surface for about five seconds and answer, without
reading every word —

- What is this product/screen for?
- What is the one action this screen wants me to take?
- Who is this for (does it signal the right audience)?

If any answer requires hunting, scrolling, or re-reading, that is a
first-impression failure even if every element individually renders
correctly. Worked example: `examples/web-homepage-five-second-check.md`.
Applies most heavily to marketing/landing pages and first-launch/empty
states; a deep authenticated settings screen is not held to the same
5-second bar, but should still communicate its purpose without a
walkthrough.

## 2. Visual hierarchy / clarity

- Is there one obvious primary action per screen (not three
  equally-weighted competing CTAs)?
- Does size/weight/color/spacing correctly signal importance — or does a
  secondary element (a footer link, a decorative icon) visually outweigh
  the primary action?
- Is related content grouped, and unrelated content separated, by spacing
  and alignment rather than by border/box overuse?
- Does text contrast and type scale make body copy and headings
  distinguishable at a glance?

## 3. Responsive / input-mode behavior

- Capture the same surface at desktop, tablet, and mobile viewport widths
  (see the adapter reference for concrete breakpoints) — check for
  overlapping elements, cut-off text, horizontal scroll that shouldn't
  exist, and controls that shrink below a comfortably tappable size.
- Confirm touch-sized targets on mobile/tablet (not just "renders", but
  "is plausibly tappable" — no controls crowded into a few pixels).
- Confirm keyboard-only navigation reaches every interactive element in a
  sane tab order on desktop, and that focus is visible at each stop.
- Check orientation where relevant (portrait/landscape on mobile/tablet;
  native apps especially, per `native-apple-adapter.md`).

## 4. Accessibility

- Every interactive element has a real accessible name (not an empty
  `alt=""` on a meaningful image, not an icon-only button with no label).
- Focus order is logical and focus is never trapped or lost after an
  interaction (a modal close, a route change).
- Color is never the only signal (an error state, a required field, a
  status) — check for a redundant icon/text cue.
- Text contrast meets a legible minimum against its actual background, not
  just against a design-system swatch in isolation.
- Reduced-motion preference is honored (see dimension 5) and screen-reader
  landmark/heading structure is sane (headings in order, one primary
  landmark per region) where the adapter can gather that evidence.

## 5. Motion / interaction feedback

- Every user action that takes non-trivial time (a submit, a load, a
  navigation) gives visible feedback — a loading state, a disabled button,
  a spinner — never a silent dead click.
- Animations serve orientation (showing where content came from/went to)
  rather than existing purely decoratively in a way that delays the user.
- Reduced-motion mode is explicitly tested, not assumed: capture the
  surface with the platform's reduced-motion preference enabled and
  confirm essential motion is suppressed or substituted, not just
  slowed down.
- No motion masks or delays an error state — a failure must still surface
  promptly even mid-animation.

## 6. Usability / task friction

- Walk one realistic user task end-to-end on the rendered surface (not
  just "does the API succeed") and count avoidable friction: unnecessary
  steps, unclear labels, a destructive action with no confirmation, a form
  that loses input on a validation error.
- Error messages are specific and actionable ("Email already in use" vs. a
  generic "Something went wrong").
- Empty/zero/error states are designed, not blank — a list with no items
  yet should explain what to do next, not just render nothing.

## 7. Performance / perceived quality

- Does the surface render meaningful content quickly, or does the user
  stare at a blank screen/layout shift while assets settle? Capture
  visible layout shift (content jumping after images/fonts load) — this is
  a perceived-quality defect even when raw load time is acceptable.
- Are there visible rendering artifacts: flash-of-unstyled-content, broken
  images, missing fonts falling back visibly, a console full of runtime
  errors during a normal interaction (capture per the adapter reference)?
- Does perceived responsiveness hold up under a normal interaction
  sequence (click, type, navigate) — not just on first paint?

## Recording findings

For each dimension actually evaluated, record: what was checked, what
surface/state/device it was checked on, and a concrete observation (not
just "looks fine"). A dimension not evaluated (e.g. accessibility skipped
because the adapter couldn't gather an accessibility tree) must be recorded
as not evaluated, never silently omitted — see the verdict format in
`examples/web-homepage-five-second-check.md`.
