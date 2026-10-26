import json

from td.fake_hands import DEFAULT_SOURCE, hands_packet, positions


def test_many_hands_share_one_source_with_distinct_subjects():
    frame = json.loads(hands_packet(3, "session", positions(0.0, 8)))["frames"][0]
    assert frame["source_id"] == DEFAULT_SOURCE and frame["sequence"] == 3
    assert [sample["subject_id"] for sample in frame["samples"]] == [f"hand:{number}" for number in range(1, 9)]
    assert all(sample["sample_id"] == "position_uv" and sample["space"] == "mediapipe_image_uv"
               and all(0 <= value <= 1 for value in sample["value"]) for sample in frame["samples"])


def test_simulated_hands_stay_apart():
    points = positions(1.0, 8)
    closest = min(((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5
                  for index, a in enumerate(points) for b in points[index + 1:])
    assert closest > 0.1
