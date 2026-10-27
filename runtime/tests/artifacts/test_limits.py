import pytest

from verilex_runtime.artifacts.limits import (
    MAX_ARTIFACT_BYTES,
    MAX_ARTIFACT_BYTES_PER_CALL,
    MAX_ARTIFACT_BYTES_PER_TURN,
    MAX_ARTIFACTS_PER_CALL,
    MAX_ARTIFACTS_PER_TURN,
    MAX_VALIDATION_REPORT_BYTES,
    validate_artifact_output_limits,
)


@pytest.mark.parametrize(
    ("count", "total_bytes"),
    [
        (0, 0),
        (1, MAX_ARTIFACT_BYTES),
        (MAX_ARTIFACTS_PER_CALL, MAX_ARTIFACT_BYTES_PER_CALL),
    ],
)
def test_call_limits_accept_exact_boundaries(count, total_bytes):
    validate_artifact_output_limits(
        artifact_count=count,
        total_bytes=total_bytes,
        scope="call",
    )


@pytest.mark.parametrize(
    ("count", "total_bytes"),
    [
        (MAX_ARTIFACTS_PER_CALL + 1, 0),
        (1, MAX_ARTIFACT_BYTES + 1),
        (1, MAX_ARTIFACT_BYTES_PER_CALL + 1),
    ],
)
def test_call_limits_reject_first_value_above_boundary(count, total_bytes):
    with pytest.raises(ValueError, match="artifact_output_limit"):
        validate_artifact_output_limits(
            artifact_count=count,
            total_bytes=total_bytes,
            scope="call",
        )


def test_turn_and_report_limits_match_approved_contract():
    assert MAX_ARTIFACT_BYTES == 100 * 1024 * 1024
    assert MAX_ARTIFACTS_PER_CALL == 10
    assert MAX_ARTIFACT_BYTES_PER_CALL == 250 * 1024 * 1024
    assert MAX_ARTIFACTS_PER_TURN == 20
    assert MAX_ARTIFACT_BYTES_PER_TURN == 500 * 1024 * 1024
    assert MAX_VALIDATION_REPORT_BYTES == 128 * 1024

    validate_artifact_output_limits(
        artifact_count=MAX_ARTIFACTS_PER_TURN,
        total_bytes=MAX_ARTIFACT_BYTES_PER_TURN,
        scope="turn",
    )
    with pytest.raises(ValueError, match="artifact_output_limit"):
        validate_artifact_output_limits(
            artifact_count=MAX_ARTIFACTS_PER_TURN + 1,
            total_bytes=0,
            scope="turn",
        )
