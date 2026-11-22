"""Post text for each platform, built from the source video's metadata.

    YouTube   description + credit (name + link) + hashtags          (title is a separate field)
    Facebook  title + description + credit (name + link) + hashtags  (the feed shows only this text)
    Instagram title + credit (name, no link: captions aren't clickable) + hashtags
    TikTok    title + credit (name) + hashtags                        (the post "title" field)
    X         title + hashtags                                        (a link makes a post cost $0.20)

Credit: "<CAPTION_CREDIT_LABEL>: <channel>" (default "المصدر"), off with ADD_SOURCE_CREDIT=false.
Hashtags: CAPTION_HASHTAGS, the first N per platform (HASHTAG_LIMITS); tags already in the title or
description are not repeated.
"""

from __future__ import annotations

import re

from . import config

HASHTAG_LIMITS = {"youtube": 3, "facebook": 3, "instagram": 5, "tiktok": 5, "x": 2}
DIVIDER = "———"


def _clean(text: str | None) -> str:
    return (text or "").strip()


def _no_links(text: str) -> str:
    return " ".join(w for w in text.split() if "://" not in w and not w.startswith("www."))


def hashtags(platform: str, *already: str) -> str:
    present = {t.lower() for part in already for t in re.findall(r"#\w+", part or "")}
    tags = [t for t in config.CAPTION_HASHTAGS if t.lower() not in present]
    return " ".join(tags[: HASHTAG_LIMITS.get(platform, 3)])


def credit(video: dict, with_link: bool) -> str:
    if not config.ADD_SOURCE_CREDIT:
        return ""
    channel = _clean(video.get("channel_name"))
    line = f"{config.CAPTION_CREDIT_LABEL}: {channel}" if channel else ""
    url = _clean(video.get("url")) if with_link else ""
    return "\n".join(p for p in (line, url) if p)


def _join(*blocks: str) -> str:
    return "\n\n".join(b for b in blocks if b)


def youtube_description(video: dict) -> str:
    body = _clean(video.get("description"))
    c = credit(video, with_link=True)
    return _join(body, f"{DIVIDER}\n{c}" if c else "", hashtags("youtube", video.get("title", ""), body))


def facebook_text(video: dict) -> str:
    title, body = _clean(video.get("title")), _clean(video.get("description"))
    c = credit(video, with_link=True)
    return _join(title, body, f"{DIVIDER}\n{c}" if c else "", hashtags("facebook", title, body))


def instagram_caption(video: dict) -> str:
    title = _clean(video.get("title"))
    return _join(title, credit(video, with_link=False), hashtags("instagram", title))


def tiktok_title(video: dict) -> str:
    title = _no_links(_clean(video.get("title")))
    return _join(title, credit(video, with_link=False), hashtags("tiktok", title))


def x_text(video: dict, limit: int) -> str:
    title = _no_links(_clean(video.get("title")))
    tags = hashtags("x", title)
    if not tags:
        return title[:limit]
    room = limit - len(tags) - 1
    return f"{title[:room].rstrip()} {tags}".strip() if room > 0 else title[:limit]
