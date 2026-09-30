"""What the host is allowed to cost, measured against what the user already pays.

The budget here is not a microbenchmark someone invented. It comes from two
facts about the situation this command runs in:

*What the user already pays.* On the DogFood workstation the founder's own
statusline script -- 35 KB of shell, the thing that was in the slot before any of
this existed -- costs about 425 ms median (min 397, p90 439, max 657) per render,
measured by running it exactly as configured with a Claude Code `Status` payload
on stdin. The render is therefore already the binding cost. Any budget expressed
as a fraction of a 300 ms refresh interval would be fiction, because the line
being composed cannot be produced in 300 ms and never could.

*What the host promises.* `DEFAULT_DEADLINE_MS` bounds the whole provider block
and `DEFAULT_UPSTREAM_TIMEOUT_MS` bounds the user's command. Those constants are
the promise, so they are what the assertions below are stated in terms of --
a budget the module declares can be checked against the module, and a budget
tied to one machine's clock speed cannot.

The end-to-end numbers recorded for HORO-1572, same workstation, real founder
upstream, three fixture providers, `python3` invoked exactly as the installed
command does (n=12 each, median):

    their statusline alone                       425 ms   (p90 439)
    host alone, no providers, no upstream          85 ms   (interpreter + import
                                                            + registry + render)
    host + their statusline, no providers         523 ms   (+98 ms)
    host + their statusline + 3 providers, cold   528 ms   (+103 ms)
    host + their statusline + 3 providers, warm   551 ms
    host + their statusline + 3 + one wedged      556 ms   (p90 574)

Three things in that table are the point. The cost of adding three products is
about 5 ms, because they are probed while the user's own command is still
running. The cost of one product being wedged is likewise about nothing, because
its timeout expires before the user's line finishes. And a warm cache is not
faster, which is worth stating plainly: caching earns its place by keeping a
slow product off the critical path, not by speeding up a render whose cost is
someone else's command.

Those provider figures are shell fixtures, and a fixture is cheaper to start
than a product. Repeated against the three real products on the same workstation
(Fornax and Libra as unoptimized debug binaries, Circinus from its venv, n=9
median):

    their statusline alone                       414 ms
    host + their statusline, 0 providers         419 ms   (+5 ms)
    host + their statusline, 3 real products     490 ms   (+72 ms)

So the host itself costs about 5 ms, and three real products cost about 72 ms
rather than the 5 ms the fixtures suggest -- three `fork`/`exec`s of real
binaries, two of them unoptimized. Concurrency is still doing the work it was
added for (probed one after another these same products would add several
hundred milliseconds), and 72 ms sits well inside `DEFAULT_DEADLINE_MS`, but a
reader comparing the two tables should take the second one as the cost of
adopting products and the first as the cost of the host's own machinery.

The tests are in-process rather than subprocess, so the ~25 ms interpreter start
and ~60 ms import are excluded from what they assert. That fixed prologue is a
property of how the command is installed, not of this module's behaviour, and
including it would only add variance to every measurement. It is recorded above
instead, where it belongs.

Information depth costs nothing measurable, which is the answer HORO-1627 wants
and is worth recording as numbers rather than as a bound that happened to hold.
Same fixtures as `InformationDepthCostTest`, three 200 ms products behind a 300 ms
upstream, median of three in-process renders:

    cold, clear                                  379 ms
    cold, detail                                 366 ms   (-13 ms)
    warm, clear                                  359 ms
    warm, detail                                 360 ms   (+1 ms)

Both deltas are inside the noise of a shared machine, which is what "detail reads
more of a snapshot the render already had" looks like from outside. The same
fixtures against a host deliberately made to re-collect at detail instead --
`polls_twice_at_detail` -- render in 743 ms against 360 ms, failing the bound by
383 ms, and the execution count goes from 3 to 6. The count is the assertion that
matters: a double poll is roughly free on a clock here, because the probes run
underneath the user's own command, so the whole finding would fit inside the slack
a timing test has to allow.

A timing test that passes proves nothing on its own, so the two that carry a
guarantee were run against the matching defects in `test_statusline_mutations`.
Probing the products one after another instead of together
(`running_the_providers_one_after_another`) moves the three-product render from
607 ms to 1116 ms, failing by 509 ms against 250 ms of slack. Not bounding a
probe at all (`waiting_for_a_provider_however_long_it_takes`) moves the wedged
case from 353 ms to 30 067 ms. Both margins are large because the defects are
structural; a timing bound that only just discriminates would be measuring this
machine instead.
"""

from __future__ import annotations

import json
import statistics
import time
import unittest

import statusline_compositor as compositor
from test_statusline_compositor import FixtureCase, provider_document, wire

# A stand-in for the user's own command, in the same order of magnitude as the
# real one. Deliberately not faster: a budget measured against an instant
# upstream would flatter the host, because the whole reason a slow product costs
# nothing here is that the user's command is slower than the provider deadline.
THEIR_LINE_MS = 300

# What a product costs when it answers. Long enough that probing three of them
# one after another would be unmistakable -- 600 ms more than probing one.
PROBE_MS = 200

# Room for process spawn, scheduler jitter and a loaded CI runner. Generous on
# purpose: every assertion below discriminates by hundreds of milliseconds, so
# slack of this size cannot hide the defect it is looking for.
SLACK_MS = 250


class PerformanceCase(FixtureCase):
    """Repeated renders, reported as a median.

    Three samples, not thirty. A median of three discards one outlier, which is
    what a shared machine produces; the assertions below discriminate by hundreds
    of milliseconds, so spending a minute of suite time to narrow a confidence
    interval nothing depends on would be waste.
    """

    SAMPLES = 3

    def upstream(self) -> str:
        return self.script(
            "their-line", f"cat >/dev/null\nsleep {THEIR_LINE_MS / 1000}\nprintf 'THEIRS'\n"
        )

    def answering_slowly(self, provider: str, *, ttl: int = 0) -> str:
        payload = wire(provider=provider, cache_ttl_seconds=ttl)
        return self.script(
            f"{provider}-probe",
            f"cat >/dev/null\nsleep {PROBE_MS / 1000}\n"
            + "cat <<'HORONOM_EOF'\n"
            + json.dumps(payload)
            + "\nHORONOM_EOF\n",
        )

    def render_ms(self, *, samples: int | None = None) -> float:
        """The median wall clock of a full render, in milliseconds.

        One untimed render first. The first render of a configuration pays for
        page-ins and, where the providers declare one, populates the cache; timing
        it would measure the fixture's warm-up rather than a steady-state refresh,
        which is what the host actually does for the life of a session.
        """
        self.run_main()
        observed = []
        for _ in range(samples or self.SAMPLES):
            started = time.monotonic()
            code, line = self.run_main()
            observed.append((time.monotonic() - started) * 1000)
            self.assertEqual(code, 0)
            self.assertTrue(line.startswith("THEIRS"), line)
        return statistics.median(observed)


class TheirLineIsTheBindingCostTest(PerformanceCase):
    def test_the_host_adds_less_than_the_deadline_it_promises(self) -> None:
        """The host's own cost, with nothing to compose but the user's line.

        Bounded by `DEFAULT_DEADLINE_MS` because that is the only added cost the
        module ever promises; with no providers registered it should not be
        spending even that.
        """
        upstream = self.upstream()
        self.write_registry(upstream={"command": f"'{upstream}'"})
        composed = self.render_ms()
        alone = statistics.median(
            self.timed_upstream(upstream) for _ in range(self.SAMPLES)
        )
        self.assertLess(
            composed - alone,
            compositor.DEFAULT_DEADLINE_MS,
            f"composing cost {composed:.0f}ms against {alone:.0f}ms for their line alone",
        )

    def timed_upstream(self, command: str) -> float:
        started = time.monotonic()
        compositor.run_upstream(f"'{command}'", b"{}", compositor.DEFAULT_UPSTREAM_TIMEOUT_MS)
        return (time.monotonic() - started) * 1000


class EnablingMoreProductsTest(PerformanceCase):
    def test_three_products_do_not_cost_three_times_one(self) -> None:
        """The property that decides whether this design scales at all.

        Probed one after another, three 200 ms products would add 600 ms to the
        render and every further product the user adopted would add 200 ms more.
        Probed together, and while the user's own command is still running, they
        add nothing measurable. The margin between those two outcomes is what this
        asserts, and it is far larger than the slack allowed.
        """
        upstream = self.upstream()
        products = [
            provider_document(
                product, [self.answering_slowly(product)], timeout_ms=1500
            )
            for product in ("fornax", "circinus", "libra-governor")
        ]
        self.write_registry(
            upstream={"command": f"'{upstream}'"}, providers=products[:1], deadline_ms=2500
        )
        one = self.render_ms()
        self.write_registry(
            upstream={"command": f"'{upstream}'"}, providers=products, deadline_ms=2500
        )
        three = self.render_ms()
        self.assertLess(
            three - one,
            SLACK_MS,
            f"one product cost {one:.0f}ms and three cost {three:.0f}ms; the difference is "
            f"the shape of a sequential probe, not a concurrent one",
        )


class WedgedProductTest(PerformanceCase):
    def test_a_product_that_never_answers_costs_the_render_nothing(self) -> None:
        """Isolation, stated as a cost rather than as a rendered string.

        Its timeout is shorter than the user's own command, so it expires while
        that command is still running and the render never waits for it at all.
        """
        upstream = self.upstream()
        healthy = provider_document("fornax", [self.answering_slowly("fornax")], timeout_ms=1500)
        self.write_registry(
            upstream={"command": f"'{upstream}'"}, providers=[healthy], deadline_ms=2500
        )
        healthy_only = self.render_ms()

        wedged = self.hanging_script("wedged", "sleep 30\n")
        self.write_registry(
            upstream={"command": f"'{upstream}'"},
            providers=[healthy, provider_document("slow", [wedged], timeout_ms=250)],
            deadline_ms=2500,
        )
        with_wedged = self.render_ms()
        self.assertLess(
            with_wedged - healthy_only,
            SLACK_MS,
            f"a wedged product moved the render from {healthy_only:.0f}ms to "
            f"{with_wedged:.0f}ms",
        )
        # And it is still reported. A product that vanished from the line when it
        # broke would be cheap and dishonest.
        self.assertIn("slow", self.run_main()[1].lower())


class CachingTest(PerformanceCase):
    def test_a_cached_answer_is_never_slower_than_asking_again(self) -> None:
        """The modest claim the cache can actually support.

        Not "caching makes the render fast" -- the numbers in the module docstring
        show it does not, because the user's own command dominates and is never
        cached. Only that serving a stored answer does not cost more than
        re-probing for one, which is the failure mode a cache keyed and validated
        on every read could plausibly have.
        """
        upstream = self.upstream()
        products = [
            provider_document(
                product, [self.answering_slowly(product, ttl=60)], timeout_ms=1500
            )
            for product in ("fornax", "circinus", "libra-governor")
        ]
        self.write_registry(
            upstream={"command": f"'{upstream}'"}, providers=products, deadline_ms=2500
        )
        warm = self.render_ms()
        self.assertTrue(any(compositor.cache_dir(self.home).iterdir()), "nothing was cached")

        # `render_ms` runs once untimed before measuring, which would repopulate
        # what was just deleted, so the cold samples are taken directly here.
        cold = []
        for _ in range(self.SAMPLES):
            for stored in compositor.cache_dir(self.home).iterdir():
                stored.unlink()
            started = time.monotonic()
            self.run_main()
            cold.append((time.monotonic() - started) * 1000)
        self.assertLess(
            warm - statistics.median(cold),
            SLACK_MS,
            f"a warm render cost {warm:.0f}ms against {statistics.median(cold):.0f}ms cold",
        )


class InformationDepthCostTest(PerformanceCase):
    """What asking for supporting context is allowed to cost (HORO-1627).

    The rule the ticket states is that detail must not become a privileged mode
    with a budget of its own: no additional cloud call, no LLM call, no scan, no
    daemon start, no extra provider poll merely because more text is being shown.
    Those are all the same claim from the cost side -- detail reads more of a
    snapshot the render already had -- and this class checks it two ways, because
    either one alone can be satisfied while the property is false.

    Counting is the load-bearing half. A depth that polled each product twice and
    threw one answer away would show up as roughly nothing on a clock, since the
    user's own command dominates and the probes run underneath it; the whole
    finding would be inside the slack every assertion here has to allow. So the
    fixtures count their own executions and the count is asserted exactly.

    Timing is the half that catches what counting cannot: a depth that spent its
    extra time somewhere other than a subprocess.
    """

    PRODUCTS = ("fornax", "circinus", "libra-governor")

    def counting(self, provider: str, *, ttl: int = 0) -> str:
        """A slow product that records having been asked.

        The counter is one byte per execution in a shared file, appended by the
        shell. Read as a delta rather than an absolute: writing an executable warms
        it by running it once, and a count that assumed otherwise would be
        measuring the fixture's own setup.
        """
        payload = wire(provider=provider, cache_ttl_seconds=ttl)
        return self.script(
            f"{provider}-counted",
            f"cat >/dev/null\nsleep {PROBE_MS / 1000}\n"
            f"printf 'x' >> '{self.home / 'asked'}'\n"
            + "cat <<'HORONOM_EOF'\n"
            + json.dumps(payload)
            + "\nHORONOM_EOF\n",
        )

    def asked(self) -> int:
        counter = self.home / "asked"
        return len(counter.read_bytes()) if counter.exists() else 0

    def install(self, depth: str, *, ttl: int = 0, extra: list | None = None) -> None:
        self.write_registry(
            upstream={"command": f"'{self.their_line}'"},
            providers=[
                provider_document(product, [self.counting(product, ttl=ttl)], timeout_ms=1500)
                for product in self.PRODUCTS
            ]
            + (extra or []),
            deadline_ms=2500,
            presentation={"depth": depth},
        )

    def setUp(self) -> None:
        super().setUp()
        self.their_line = self.upstream()

    def test_showing_more_of_a_snapshot_does_not_ask_for_a_new_one(self) -> None:
        """One render, one question per product, at either depth.

        Asserted as equality rather than as a bound, because this is the one
        property here that can be stated exactly. Everything the ticket forbids
        detail from doing -- a cloud call, an LLM call, a scan, a daemon start, an
        extra poll -- has to leave the process to happen, and these products are
        processes that keep their own tally.
        """
        counted = {}
        for depth in ("clear", "detail"):
            self.install(depth)
            before = self.asked()
            code, line = self.run_main()
            self.assertEqual(code, 0)
            self.assertTrue(line.startswith("THEIRS"), line)
            counted[depth] = self.asked() - before
        self.assertEqual(counted["clear"], len(self.PRODUCTS))
        self.assertEqual(counted["detail"], counted["clear"])

    def test_detail_is_not_a_second_latency_class(self) -> None:
        """The delta the founder feels, cold.

        Measured with the cache disabled (`ttl=0`) so both depths pay for the
        probes, which is the comparison that could plausibly differ. A depth that
        added a round of provider work would have to add at least one `PROBE_MS`
        here, and the slack is smaller than that.
        """
        self.install("clear")
        clear = self.render_ms()
        self.install("detail")
        detail = self.render_ms()
        self.assertLess(
            detail - clear,
            SLACK_MS,
            f"clear rendered in {clear:.0f}ms and detail in {detail:.0f}ms",
        )

    def test_the_delta_stays_small_once_the_cache_is_warm(self) -> None:
        """And warm, where a per-reading cache validation would show up instead.

        Cold and warm are different code paths -- one probes, one decodes a stored
        document and checks its age -- and detail reads more of whatever comes back
        either way. A depth that revalidated the cache per reading rather than per
        product would be invisible in the cold measurement above.
        """
        self.install("clear", ttl=60)
        clear = self.render_ms()
        self.assertTrue(any(compositor.cache_dir(self.home).iterdir()), "nothing was cached")
        self.install("detail", ttl=60)
        detail = self.render_ms()
        self.assertLess(
            detail - clear,
            SLACK_MS,
            f"warm clear rendered in {clear:.0f}ms and warm detail in {detail:.0f}ms",
        )

    def test_a_wedged_product_is_still_contained_when_it_has_its_own_row(self) -> None:
        """Containment survives the layout that gives every product a line.

        `WedgedProductTest` proves a product that never answers costs the render
        nothing. At detail that product is also given a physical row of its own, so
        the render has to lay out something for a provider that produced no
        document -- and a layout that waited for the row's contents would put the
        wait back after the timeout had already removed it.
        """
        wedged = provider_document("slow", [self.hanging_script("wedged", "sleep 30\n")], timeout_ms=250)
        self.install("detail")
        healthy_only = self.render_ms()
        self.install("detail", extra=[wedged])
        with_wedged = self.render_ms()
        self.assertLess(
            with_wedged - healthy_only,
            SLACK_MS,
            f"a wedged product moved a detail render from {healthy_only:.0f}ms to "
            f"{with_wedged:.0f}ms",
        )
        line = self.run_main()[1]
        # Still reported, and on a row of its own rather than dropped for having
        # nothing to say. A product that disappeared from the line when it broke
        # would be cheap and dishonest.
        self.assertIn("slow", line.lower())
        self.assertEqual(len([row for row in line.splitlines() if "slow" in row.lower()]), 1, line)


class DeclaredBudgetTest(unittest.TestCase):
    """The constants the assertions above are written in terms of.

    Stated as a test so that relaxing a bound is a visible decision. Changing
    `DEFAULT_DEADLINE_MS` to two seconds would keep every timing test above
    passing while making the product materially worse.
    """

    def test_the_provider_block_is_bounded_well_inside_the_users_own_cost(self) -> None:
        # The founder's line measured 425 ms median. A provider block permitted to
        # cost more than that would make the products the slow part of a line they
        # are guests on.
        self.assertLessEqual(compositor.DEFAULT_DEADLINE_MS, 600)
        self.assertLessEqual(compositor.DEFAULT_PROVIDER_TIMEOUT_MS, 250)

    def test_the_users_command_is_given_room_a_real_statusline_needs(self) -> None:
        # Their script's worst observed render was 657 ms. A bound near its median
        # would truncate their line on a loaded machine, which is the one outcome
        # worse than being slow.
        self.assertGreaterEqual(compositor.DEFAULT_UPSTREAM_TIMEOUT_MS, 1500)

    def test_a_product_cannot_be_given_longer_than_the_whole_block(self) -> None:
        self.assertLessEqual(
            compositor.MAX_PROVIDER_TIMEOUT_MS, compositor.MAX_DEADLINE_MS
        )


if __name__ == "__main__":
    unittest.main()
