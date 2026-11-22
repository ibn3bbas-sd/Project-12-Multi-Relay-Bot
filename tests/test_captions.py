from unittest import mock

from src import captions, config, fb_uploader, ig_uploader, uploader, x_uploader
from src import tiktok_uploader as tt

VIDEO = {"id": "v1", "title": "حكم إيداع المال في البنك", "description": "شرح مفصل #فقه",
         "channel_name": "القناة المصدر", "url": "https://www.youtube.com/watch?v=v1"}


def test_youtube_description_has_body_credit_link_and_three_new_tags():
    text = uploader._build_description(VIDEO)
    assert text.startswith("شرح مفصل #فقه")
    assert "المصدر: القناة المصدر\nhttps://www.youtube.com/watch?v=v1" in text
    assert text.endswith("#فتاوى #إسلام #سؤال_وجواب")   # #فقه already in the description


def test_facebook_leads_with_title():
    text = fb_uploader._build_description(VIDEO)
    assert text.startswith("حكم إيداع المال في البنك\n\nشرح مفصل")
    assert "https://www.youtube.com/watch?v=v1" in text


def test_instagram_and_tiktok_credit_without_link_and_five_tags():
    for text in (ig_uploader.build_caption(VIDEO), tt.build_title(VIDEO)):
        assert "المصدر: القناة المصدر" in text
        assert "https://" not in text
        assert text.split("\n\n")[-1] == "#فتاوى #فقه #إسلام #سؤال_وجواب #دروس_علمية"


def test_x_is_title_plus_two_tags_within_limit():
    assert x_uploader.build_text(VIDEO) == "حكم إيداع المال في البنك #فتاوى #فقه"
    long = {**VIDEO, "title": "ع" * 400}
    text = x_uploader.build_text(long)
    assert len(text) <= x_uploader.MAX_TEXT and text.endswith("#فتاوى #فقه")


def test_credit_off():
    with mock.patch.object(config, "ADD_SOURCE_CREDIT", False):
        assert "المصدر" not in captions.facebook_text(VIDEO)
        assert "youtube.com" not in captions.youtube_description(VIDEO)


def test_youtube_angle_brackets_replaced():
    assert "<" not in uploader._build_description({**VIDEO, "description": "a <b> c"})
