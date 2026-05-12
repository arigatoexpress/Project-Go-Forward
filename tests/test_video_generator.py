from tools.video_generator import _normalize_veo_duration


def test_normalize_veo_duration_uses_supported_short_form_values():
    assert _normalize_veo_duration(5) == 6
    assert _normalize_veo_duration(4) == 4
    assert _normalize_veo_duration(8) == 8
    assert _normalize_veo_duration(30) == 8
