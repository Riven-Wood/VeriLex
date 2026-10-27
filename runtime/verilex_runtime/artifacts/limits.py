MAX_ARTIFACT_BYTES = 100 * 1024 * 1024
MAX_ARTIFACTS_PER_CALL = 10
MAX_ARTIFACT_BYTES_PER_CALL = 250 * 1024 * 1024
MAX_ARTIFACTS_PER_TURN = 20
MAX_ARTIFACT_BYTES_PER_TURN = 500 * 1024 * 1024
MAX_VALIDATION_REPORT_BYTES = 128 * 1024


def validate_artifact_output_limits(
    *, artifact_count: int, total_bytes: int, scope: str
) -> None:
    if artifact_count < 0 or total_bytes < 0:
        raise ValueError("artifact_output_limit")
    if total_bytes > artifact_count * MAX_ARTIFACT_BYTES:
        raise ValueError("artifact_output_limit")
    if scope == "call":
        max_count = MAX_ARTIFACTS_PER_CALL
        max_bytes = MAX_ARTIFACT_BYTES_PER_CALL
    elif scope == "turn":
        max_count = MAX_ARTIFACTS_PER_TURN
        max_bytes = MAX_ARTIFACT_BYTES_PER_TURN
    else:
        raise ValueError("artifact_output_limit")
    if artifact_count > max_count or total_bytes > max_bytes:
        raise ValueError("artifact_output_limit")
