import uuid

from commons.domain.outcomes import OutcomeKind, OutcomeRecord, Report, merge_report


def make_report(
    outcome: OutcomeKind | None = None, evidence: dict[str, object] | None = None
) -> Report:
    return Report(
        task_id=uuid.uuid4(),
        entry_id=uuid.uuid4(),
        agent_assessment="Helped: the pin fixed the import.",
        outcome=outcome,
        evidence=evidence,
    )


def test_first_report_without_outcome_is_unknown() -> None:
    record = merge_report(None, make_report())
    assert record == OutcomeRecord("unknown", {}, "Helped: the pin fixed the import.")
    assert record.outcome_source() is None


def test_agent_outcome_is_used_when_nothing_was_observed() -> None:
    record = merge_report(None, make_report("tests_passed", {"command": "pytest"}))
    assert record.outcome == "tests_passed"
    assert record.evidence == {"agent": {"command": "pytest"}, "outcome_source": "agent"}


def test_observed_outcome_is_never_overwritten() -> None:
    observed = OutcomeRecord(
        "tests_failed", {"outcome_source": "hook", "observed": {"exit_code": 1}}, None
    )
    record = merge_report(observed, make_report("tests_passed", {"claim": "it worked"}))
    assert record.outcome == "tests_failed"
    assert record.evidence == {
        "outcome_source": "hook",
        "observed": {"exit_code": 1},
        "agent": {"claim": "it worked"},
    }
    assert record.agent_assessment == "Helped: the pin fixed the import."


def test_later_agent_report_replaces_earlier_self_report() -> None:
    first = merge_report(None, make_report("tests_failed"))
    second = merge_report(first, make_report("tests_passed"))
    assert second.outcome == "tests_passed"


def test_report_without_outcome_keeps_existing_outcome() -> None:
    first = merge_report(None, make_report("error_resolved"))
    assert merge_report(first, make_report()).outcome == "error_resolved"
