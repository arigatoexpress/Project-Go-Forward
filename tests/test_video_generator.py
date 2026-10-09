from tools.video_generator import _normalize_veo_duration


def test_normalize_veo_duration_uses_supported_short_form_values():
    assert _normalize_veo_duration(5) == 6
    assert _normalize_veo_duration(4) == 4
    assert _normalize_veo_duration(8) == 8
    assert _normalize_veo_duration(30) == 8


def test_resize_clip_uses_current_pillow_for_portrait_and_landscape():
    import numpy as np
    from moviepy.editor import ImageClip

    from tools.video_generator import resize_clip_to_fill

    for height, width in ((20, 40), (40, 20)):
        source = ImageClip(np.full((height, width, 4), 255, dtype=np.uint8))
        result = resize_clip_to_fill(source, 32, 48)
        try:
            assert result.get_frame(0).shape == (48, 32, 3)
            assert result.mask.get_frame(0).shape == (48, 32)
            assert np.all(result.mask.get_frame(0) == 1)
        finally:
            result.close()
            source.close()
