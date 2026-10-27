from verilex_runtime.agent.limits import DEFAULT_TURN_LIMITS, TurnLimits


def test_default_turn_limits():
    assert DEFAULT_TURN_LIMITS.max_steps == 30
    assert DEFAULT_TURN_LIMITS.wall_clock_seconds == 30 * 60


def test_custom_turn_limits():
    limits = TurnLimits(max_steps=1, wall_clock_seconds=0.001)
    assert limits.max_steps == 1
    assert limits.wall_clock_seconds == 0.001
