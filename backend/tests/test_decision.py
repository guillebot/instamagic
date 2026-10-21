import pytest
from instamagic.models.enums import SafetyState
from instamagic.services.safety.decision import DecisionInput, decide


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (DecisionInput(False, True, False, True, True), SafetyState.REVIEW),
        (DecisionInput(True, False, False, False, False), SafetyState.REVIEW),
        (DecisionInput(True, True, True, False, False), SafetyState.REVIEW),
        (DecisionInput(True, True, False, False, False), SafetyState.REVIEW),
        (DecisionInput(True, True, False, True, True), SafetyState.SAFE_AFTER_CROP),
        (DecisionInput(True, True, False, True, False), SafetyState.DO_NOT_POST),
        (DecisionInput(True, True, True, True, False), SafetyState.DO_NOT_POST),
    ],
)
def test_decision_table(data: DecisionInput, expected: SafetyState) -> None:
    assert decide(data) is expected
