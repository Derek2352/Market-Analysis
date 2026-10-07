"""Shared topic slug: ASCII unchanged, CJK preserved, no cross-topic collisions."""
from __future__ import annotations

import pytest

from src.util_slug import slugify


@pytest.mark.parametrize("topic,expected", [
    ("MTR Mobile", "mtr_mobile"),
    ("AlipayHK", "alipayhk"),
    ("  WeChat  Pay -- HK!! ", "wechat_pay_hk"),
    ("snake_case__topic", "snake_case_topic"),
    ("", "untitled"),
    ("!!!", "untitled"),
])
def test_ascii_slugs_unchanged(topic, expected):
    assert slugify(topic) == expected


@pytest.mark.parametrize("topic,expected", [
    ("大家樂", "大家樂"),
    ("支付寶 香港", "支付寶_香港"),
    ("AlipayHK 支付寶香港", "alipayhk_支付寶香港"),
    ("八達通", "八達通"),
    ("ポケモン カード", "ポケモン_カード"),
    ("がっこう", "がっこう"),        # kana voicing marks survive
    ("카카오톡", "카카오톡"),
])
def test_non_latin_scripts_survive(topic, expected):
    assert slugify(topic) == expected


def test_distinct_cjk_topics_do_not_collide():
    topics = ["大家樂", "支付寶 香港", "八達通", "美心", "惠康"]
    slugs = {slugify(t) for t in topics}
    assert len(slugs) == len(topics)
    assert "untitled" not in slugs


def test_latin_accents_dropped_and_fullwidth_folded():
    assert slugify("Café de Coral") == "cafe_de_coral"
    assert slugify("ＭＴＲ　Ｍｏｂｉｌｅ") == "mtr_mobile"


def test_length_capped_for_filesystem_limits():
    out = slugify("香" * 500)
    assert len(out) <= 80
    assert len(out.encode("utf-8")) < 255


def test_windows_reserved_still_suffixed():
    assert slugify("CON") == "con_topic"
    assert slugify("Connect") == "connect"
