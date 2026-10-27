import pytest

from verilex_runtime.context_usage.estimator import estimate_context_usage
from verilex_runtime.context_usage.models import ContextSegment


def test_estimate_breakdown_sums_to_total_and_uses_effective_window():
    usage = estimate_context_usage(
        (
            ContextSegment("messages", "Hello 法律"),
            ContextSegment("system_and_tools", '{"name":"read_file"}'),
        ),
        context_window_tokens=100_000,
    )

    assert sum(usage.breakdown.as_dict().values()) == usage.used_tokens
    assert usage.effective_window_tokens == 90_000
    assert usage.estimated is True


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("abcd", 5),       # one ASCII token + four protocol tokens
        ("法律", 6),       # two conservative CJK tokens + protocol
        ("{}", 5),         # one structural token + protocol
        ("", 4),           # an empty message still has protocol framing
    ],
)
def test_character_classes_have_conservative_hand_checked_costs(text, expected):
    usage = estimate_context_usage((ContextSegment("messages", text),), None)
    assert usage.used_tokens == expected


def test_images_and_categories_are_counted_without_losing_the_exact_sum():
    usage = estimate_context_usage(
        (
            ContextSegment("attachments", "scan", image_count=2),
            ContextSegment("skills", "review"),
            ContextSegment("other", "x"),
        ),
        None,
    )

    assert usage.breakdown.attachments >= 512
    assert usage.breakdown.skills > 0
    assert usage.breakdown.other > 0
    assert sum(usage.breakdown.as_dict().values()) == usage.used_tokens


@pytest.mark.parametrize(
    ("used_text", "window", "warning"),
    [
        ("a" * 2664, 1000, "normal"),
        ("a" * 2684, 1000, "near_limit"),
        ("a" * 3224, 1000, "critical"),
    ],
)
def test_warning_thresholds_use_75_and_90_percent_of_effective_capacity(
    used_text, window, warning
):
    usage = estimate_context_usage(
        (ContextSegment("messages", used_text),), window
    )
    assert usage.warning == warning


def test_unknown_capacity_never_invents_a_percentage():
    usage = estimate_context_usage((ContextSegment("messages", "hello"),), None)
    assert usage.context_window_tokens is None
    assert usage.effective_window_tokens is None
    assert usage.used_percent is None
    assert usage.warning == "normal"


def test_public_percentage_clamps_but_used_tokens_remain_truthful():
    usage = estimate_context_usage(
        (ContextSegment("messages", "法" * 1000),), 100
    )
    assert usage.used_tokens == 1004
    assert usage.used_percent == 100
    assert usage.warning == "critical"


@pytest.mark.parametrize("capacity", [0, -1, True, 1.5])
def test_invalid_capacity_is_rejected(capacity):
    with pytest.raises(ValueError):
        estimate_context_usage((), capacity)


@pytest.mark.parametrize("image_count", [-1, True, 1.5])
def test_invalid_image_count_is_rejected(image_count):
    with pytest.raises(ValueError):
        ContextSegment("attachments", "", image_count=image_count)


def test_repeated_calls_are_deterministic():
    segments = (ContextSegment("messages", "Hello，世界!"),)
    assert estimate_context_usage(segments, 128_000) == estimate_context_usage(
        segments, 128_000
    )
