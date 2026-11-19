"""livewatch editing helpers: ad trimming, sound plan, captions, layout graph, recorder cuts."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "livewatch"))

import adcheck  # noqa: E402
import audiofx  # noqa: E402
import pipeline  # noqa: E402
import recorder  # noqa: E402

WORDS = [{"start": i * 0.5, "end": i * 0.5 + 0.4, "word": f"w{i}"} for i in range(20)]


def test_clean_span_keeps_longest_non_ad_run():
    flags = [True] * 5 + [False] * 20 + [True] * 3 + [False] * 4
    assert adcheck.clean_span(flags, min_len=15) == (5, 25)


def test_clean_span_none_when_too_short():
    assert adcheck.clean_span([False] * 10 + [True] * 30, min_len=15) is None


def test_sfx_label_strips_noise():
    assert audiofx._label("BOOM Sound Effect #fyp.mp3") == "boom"
    assert audiofx._label("Wrong Answer SFX (free).wav") == "wrong-answer"


def test_clean_plan_filters_bad_items_and_spreads_effects():
    lib = {"boom": "/x/boom.mp3", "bruh": "/x/bruh.mp3"}
    raw = {"mood": "funny", "typewriter": True, "sfx": [
        {"word": 0, "sound": "boom"},            # never the first word
        {"word": 5, "sound": "nope"},            # not in the library
        {"word": 5, "sound": "boom", "punch": True},
        {"word": 6, "sound": "bruh"},            # too close to the previous one
        {"word": 12, "sound": "bruh"},
        {"word": 15, "sound": "boom"},           # over sfx_max
    ]}
    plan = audiofx.clean_plan(raw, WORDS, lib, sfx_max=2)
    assert plan["mood"] == "funny" and plan["typewriter"] is True
    assert [(s["sound"], s["punch"]) for s in plan["sfx"]] == [("boom", True), ("bruh", False)]


def test_clean_plan_unknown_mood_falls_back_to_hype():
    assert audiofx.clean_plan({"mood": "angry"}, WORDS, {}, 2)["mood"] == "hype"


def test_ensure_tags_names_channel_and_adds_tags():
    cap = pipeline.ensure_tags("what a play 🔥", "lacy", "#LACY #GTA6")
    assert cap.startswith("LACY ") and cap.endswith("#LACY #GTA6")
    assert pipeline.ensure_tags("LACY wins #lacy #gta6", "lacy", "#LACY #GTA6") == "LACY wins #lacy #gta6"


def test_facecam_graph_is_full_frame_and_even():
    graph, cam_h = pipeline.facecam_top_graph(1920, 1080, (60, 0, 400, 278))
    assert cam_h % 2 == 0 and 0 < cam_h <= int(pipeline.H * 0.42)
    assert f"scale={pipeline.W}:{cam_h}" in graph
    assert f"scale=-2:{pipeline.H - cam_h}" in graph
    assert graph.endswith("vstack[v]")


def test_captions_ass_escapes_override_braces(tmp_path):
    words = [{"start": 0.0, "end": 0.4, "word": "{\\b1}hey"}, {"start": 0.5, "end": 0.9, "word": "there"}]
    path = tmp_path / "c.ass"
    pipeline.captions_ass(words, str(path))
    body = path.read_text().split("[Events]")[1]
    assert "\\b1" not in body
    assert "HEY" in body and "THERE" in body


def test_segments_between_picks_overlapping_segments(tmp_path):
    for start in (100, 102, 104, 106, 108):
        (tmp_path / f"{start}.ts").write_bytes(b"")
    (tmp_path / "junk.ts").write_bytes(b"")
    got = [s for s, _ in recorder.segments_between(str(tmp_path), 103.5, 106.5)]
    assert got == [102, 104, 106]


def test_valid_channel():
    assert recorder.valid_channel("Lacy")
    assert not recorder.valid_channel("ab")
    assert not recorder.valid_channel("lacy; rm -rf /")
