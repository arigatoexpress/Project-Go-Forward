"""Production must serve the commit you merged.

Twice on 2026-08-31 a cutover was believed done while production still served
older code: the photo fix appeared "cut over" but the live bundle still carried
the original 4.5s watchdog, because the PR had never been merged. Nothing
anywhere compared what is DEPLOYED to what is MERGED.

Production also pins traffic (`--no-traffic --tag=candidate`), so merging alone
never promotes a revision — making this drift the normal state, not the
exception, and therefore worth an explicit probe.
"""

from scripts.production_smoke import evaluate_served_commit

MERGED = "59d3fbe1111111111111111111111111111111111"
DEPLOYED_OLD = "7a3dfef2222222222222222222222222222222222"


class TestServedCommit:
    def test_passes_when_production_serves_the_merged_commit(self):
        probe = evaluate_served_commit(
            served=MERGED, expected=MERGED, status=200, elapsed_ms=1
        )
        assert probe.ok is True
        assert MERGED[:7] in probe.evidence

    def test_fails_when_production_is_behind(self):
        probe = evaluate_served_commit(
            served=DEPLOYED_OLD, expected=MERGED, status=200, elapsed_ms=1
        )
        assert probe.ok is False
        assert DEPLOYED_OLD[:7] in probe.evidence
        assert MERGED[:7] in probe.evidence

    def test_skips_cleanly_when_no_expectation_is_supplied(self):
        # Without --expect-commit there is nothing to compare against; the probe
        # must report that plainly rather than inventing a pass.
        probe = evaluate_served_commit(
            served=DEPLOYED_OLD, expected=None, status=200, elapsed_ms=1
        )
        assert probe.ok is True
        assert "no expected commit" in probe.evidence

    def test_fails_when_the_deployed_version_is_unknown(self):
        probe = evaluate_served_commit(
            served="", expected=MERGED, status=200, elapsed_ms=1
        )
        assert probe.ok is False
        assert "unknown" in probe.evidence

    def test_accepts_a_short_sha_expectation(self):
        probe = evaluate_served_commit(
            served=MERGED, expected=MERGED[:7], status=200, elapsed_ms=1
        )
        assert probe.ok is True
