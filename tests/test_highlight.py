"""关键字高亮测试：匹配、优先级、颜色分段与配置往返。"""

from __future__ import annotations

import pytest

from serialtool.highlight import (
    Highlighter,
    HighlightRule,
    default_rules,
    parse_rules,
    serialize_rules,
)


class TestRuleValidation:
    def test_invalid_color_falls_back(self):
        assert HighlightRule(pattern="x", color="red").color == "#ef4a4e"

    def test_valid_color_kept(self):
        assert HighlightRule(pattern="x", color="#3FB950").color == "#3FB950"

    def test_from_dict_rejects_empty_pattern(self):
        assert HighlightRule.from_dict({"pattern": "  "}) is None
        assert HighlightRule.from_dict("nope") is None

    def test_bad_regex_does_not_raise(self):
        assert HighlightRule(pattern="(unclosed", regex=True).compile() is None


class TestMatching:
    def test_literal_is_case_insensitive(self):
        hl = Highlighter([HighlightRule(pattern="error", color="#ff0000")])
        spans = hl.spans("ERROR happened")
        assert spans == [(0, 5, "#ff0000")]

    def test_regex_rule(self):
        hl = Highlighter([HighlightRule(pattern=r"\d{3}", color="#00ff00", regex=True)])
        assert hl.spans("abc 123") == [(4, 7, "#00ff00")]

    def test_multiple_occurrences(self):
        hl = Highlighter([HighlightRule(pattern="ab", color="#111111")])
        assert [s[:2] for s in hl.spans("ab ab")] == [(0, 2), (3, 5)]

    def test_first_rule_wins_on_overlap(self):
        hl = Highlighter(
            [
                HighlightRule(pattern="abcdef", color="#aaaaaa"),
                HighlightRule(pattern="cde", color="#bbbbbb"),
            ]
        )
        assert hl.spans("abcdef") == [(0, 6, "#aaaaaa")]

    def test_later_rule_can_fill_gap(self):
        hl = Highlighter(
            [
                HighlightRule(pattern="abc", color="#aaaaaa"),
                HighlightRule(pattern="def", color="#bbbbbb"),
            ]
        )
        assert hl.spans("abcdef") == [(0, 3, "#aaaaaa"), (3, 6, "#bbbbbb")]

    def test_empty_text(self):
        hl = Highlighter([HighlightRule(pattern="x")])
        assert hl.spans("") == []


class TestSegments:
    def test_segments_cover_whole_text(self):
        hl = Highlighter([HighlightRule(pattern="err", color="#ff0000")])
        segments = hl.segments("an err here", "#dddddd")
        assert "".join(text for text, _ in segments) == "an err here"
        assert ("err", "#ff0000") in segments

    def test_no_rules_returns_single_segment(self):
        hl = Highlighter([])
        assert hl.segments("abc", "#dddddd") == [("abc", "#dddddd")]

    def test_disabled_returns_single_segment(self):
        hl = Highlighter([HighlightRule(pattern="a")], enabled=False)
        assert hl.is_active is False
        assert hl.segments("aaa", "#dddddd") == [("aaa", "#dddddd")]

    def test_rules_survive_disable(self):
        hl = Highlighter([HighlightRule(pattern="a")])
        hl.set_enabled(False)
        assert len(hl.rules) == 1
        hl.set_enabled(True)
        assert hl.is_active is True


class TestSerialization:
    def test_roundtrip(self):
        rules = [
            HighlightRule(pattern="error", color="#ef4a4e"),
            HighlightRule(pattern=r"^ok\b", color="#3fb950", regex=True, ignore_case=False),
        ]
        restored = parse_rules(serialize_rules(rules))
        assert restored == rules

    def test_empty_text_gives_defaults(self):
        assert len(parse_rules("")) == len(default_rules())

    def test_broken_json_gives_defaults(self):
        assert len(parse_rules("{not json")) == len(default_rules())

    def test_non_list_json_gives_defaults(self):
        assert len(parse_rules('{"a": 1}')) == len(default_rules())

    @pytest.mark.parametrize("pattern", ["error", "warn"])
    def test_defaults_contain_common_keywords(self, pattern):
        assert any(rule.pattern == pattern for rule in default_rules())
