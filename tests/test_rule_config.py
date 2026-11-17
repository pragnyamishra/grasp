"""Tests that the rule set is genuinely configurable.

These matter because the whole claim is that Grasp adapts to any codebase
without editing Python. If disabling a rule or adding your own quietly did
nothing, that claim would be false.
"""

from __future__ import annotations

import re

import pytest

from grasp.config import _parse_rules, load_settings
from grasp.diffparser import parse_diff
from grasp.errors import ConfigError
from grasp.risk import (
    DEFAULT_LARGE_ADDED,
    LineRule,
    PathRule,
    RiskLevel,
    RuleConfig,
    all_rule_codes,
    assess_file,
)


def _file(path: str, added: str, context: str = "const x = 1;"):
    text = (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        "@@ -1 +1,2 @@\n"
        f" {context}\n"
        f"+{added}\n"
    )
    return parse_diff(text).files[0]


def _codes(assessment) -> set:
    return {f.code for f in assessment.findings}


# Behaviour


def test_disabling_a_rule_removes_its_finding():
    file = _file("src/utils/parse.ts", "  // TODO fix later")
    assert "todo" in _codes(assess_file(file))

    config = RuleConfig(disabled=frozenset({"todo"}))
    assert "todo" not in _codes(assess_file(file, config))


def test_disabling_the_only_finding_leaves_the_file_clear():
    file = _file("src/utils/parse.ts", "  // TODO fix later")
    config = RuleConfig(disabled=frozenset({"todo"}))
    assessment = assess_file(file, config)
    assert assessment.level is RiskLevel.LOW
    assert "clear" in _codes(assessment)


def test_severity_override_regrades_a_finding():
    file = _file("src/components/Body.tsx", "  <div dangerouslySetInnerHTML={{ __html: b }} />")
    assert assess_file(file).level is RiskLevel.HIGH

    config = RuleConfig(severity_overrides={"react-dangerous-html": RiskLevel.LOW})
    assert assess_file(file, config).level is RiskLevel.LOW


def test_a_custom_path_rule_fires():
    file = _file("src/tenant/switcher.ts", "  const t = pick();")
    assert assess_file(file).level is RiskLevel.LOW

    config = RuleConfig(
        extra_path_rules=(
            PathRule("path-tenant", re.compile(r"(?i)tenant"), "touches tenant isolation", RiskLevel.HIGH),
        )
    )
    assessment = assess_file(file, config)
    assert assessment.level is RiskLevel.HIGH
    assert "path-tenant" in _codes(assessment)


def test_a_custom_line_rule_fires_and_is_line_anchored():
    file = _file("src/api/user.ts", "  const r = await fetch('/api/user');")
    assert assess_file(file).level is RiskLevel.LOW

    config = RuleConfig(
        extra_line_rules=(
            LineRule(
                "raw-fetch",
                re.compile(r"\bfetch\s*\("),
                "uses raw fetch instead of the shared api client",
                RiskLevel.MEDIUM,
            ),
        )
    )
    assessment = assess_file(file, config)
    assert "raw-fetch" in _codes(assessment)
    found = [f for f in assessment.findings if f.code == "raw-fetch"][0]
    assert found.line == 2


def test_a_custom_line_rule_respects_unless():
    config = RuleConfig(
        extra_line_rules=(
            LineRule(
                "raw-fetch",
                re.compile(r"\bfetch\s*\("),
                "uses raw fetch",
                RiskLevel.MEDIUM,
                unless=re.compile(r"apiClient"),
            ),
        )
    )
    plain = _file("src/api/user.ts", "  const r = await fetch('/api/user');")
    wrapped = _file("src/api/user.ts", "  const r = await apiClient.fetch('/api/user');")
    assert "raw-fetch" in _codes(assess_file(plain, config))
    assert "raw-fetch" not in _codes(assess_file(wrapped, config))


def test_large_change_threshold_is_configurable():
    added = "".join(f"+line{i}\n" for i in range(20))
    text = (
        "diff --git a/big.ts b/big.ts\n"
        "--- a/big.ts\n"
        "+++ b/big.ts\n"
        "@@ -1 +1,21 @@\n"
        " start\n" + added
    )
    file = parse_diff(text).files[0]
    # Twenty added lines is not large by default.
    assert "large-change" not in _codes(assess_file(file))
    # But a team can say it is.
    assert "large-change" in _codes(assess_file(file, RuleConfig(large_added=10)))


def test_custom_rule_codes_are_in_the_catalog():
    config = RuleConfig(
        extra_path_rules=(
            PathRule("path-tenant", re.compile("tenant"), "tenant", RiskLevel.HIGH),
        )
    )
    assert "path-tenant" in all_rule_codes(config)


# Parsing and validation


def test_parse_rules_builds_the_config():
    config = _parse_rules(
        {
            "disable": ["todo"],
            "large_added": 500,
            "severity": {"cast-any": "low"},
            "path": [
                {
                    "code": "path-tenant",
                    "pattern": "(?i)tenant",
                    "message": "touches tenant isolation",
                    "severity": "high",
                }
            ],
            "line": [
                {
                    "code": "raw-fetch",
                    "pattern": "fetch\\(",
                    "message": "uses raw fetch",
                    "unless": "apiClient",
                }
            ],
        }
    )
    assert config.disabled == frozenset({"todo"})
    assert config.large_added == 500
    assert config.severity_overrides["cast-any"] is RiskLevel.LOW
    assert config.path_rules()[-1].code == "path-tenant"
    assert config.line_rules()[-1].unless is not None


def test_defaults_when_the_rules_table_is_minimal():
    config = _parse_rules({})
    assert config.large_added == DEFAULT_LARGE_ADDED
    assert config.disabled == frozenset()


def test_unknown_rule_code_is_rejected():
    # A silent no-op would look like the setting worked.
    with pytest.raises(ConfigError) as exc:
        _parse_rules({"disable": ["todoo"]})
    assert "todoo" in str(exc.value)


def test_unknown_rule_code_in_severity_is_rejected():
    with pytest.raises(ConfigError):
        _parse_rules({"severity": {"not-a-rule": "low"}})


def test_a_custom_code_can_be_disabled_in_the_same_file():
    config = _parse_rules(
        {
            "disable": ["raw-fetch"],
            "line": [{"code": "raw-fetch", "pattern": "fetch\\(", "message": "x"}],
        }
    )
    assert "raw-fetch" in config.disabled
    assert all(r.code != "raw-fetch" for r in config.line_rules())


def test_bad_regex_is_rejected():
    with pytest.raises(ConfigError) as exc:
        _parse_rules({"line": [{"code": "x", "pattern": "([", "message": "m"}]})
    assert "regular expression" in str(exc.value)


def test_bad_severity_name_is_rejected():
    with pytest.raises(ConfigError):
        _parse_rules({"severity": {"todo": "critical"}})


def test_unknown_key_in_rules_is_rejected():
    with pytest.raises(ConfigError) as exc:
        _parse_rules({"disabel": ["todo"]})
    assert "disabel" in str(exc.value)


def test_missing_required_field_in_a_custom_rule_is_rejected():
    with pytest.raises(ConfigError):
        _parse_rules({"line": [{"code": "x", "message": "no pattern"}]})


def test_bad_large_added_is_rejected():
    with pytest.raises(ConfigError):
        _parse_rules({"large_added": 0})
    with pytest.raises(ConfigError):
        _parse_rules({"large_added": "many"})


def test_config_file_wires_all_the_way_through(tmp_path, monkeypatch):
    """A real .grasp.toml must actually change the triage."""
    (tmp_path / ".grasp.toml").write_text(
        "\n".join(
            [
                "[rules]",
                'disable = ["todo"]',
                "",
                "[[rules.path]]",
                'code = "path-tenant"',
                'pattern = "(?i)tenant"',
                'message = "touches tenant isolation"',
                'severity = "high"',
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GRASP_API_KEY", raising=False)

    settings = load_settings(use_model=False)
    assert "todo" in settings.rules.disabled

    todo_file = _file("src/utils/parse.ts", "  // TODO later")
    assert "todo" not in _codes(assess_file(todo_file, settings.rules))

    tenant_file = _file("src/tenant/switcher.ts", "  const t = pick();")
    assert "path-tenant" in _codes(assess_file(tenant_file, settings.rules))
