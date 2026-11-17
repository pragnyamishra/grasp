"""Tests for the frontend rules.

Every rule gets a positive test proving it fires and a negative test
proving the common harmless form does not. The negative tests are the
important half: a rule that cries wolf on ordinary React or TypeScript
trains people to ignore the whole tool.
"""

from __future__ import annotations

from grasp.diffparser import parse_diff
from grasp.risk import RiskLevel, assess_file


def _diff(path: str, *added: str, context: str = "const x = 1;") -> str:
    body = "".join(f"+{line}\n" for line in added)
    return (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        f"@@ -1 +1,{1 + len(added)} @@\n"
        f" {context}\n" + body
    )


def _assess(path: str, *added: str, context: str = "const x = 1;"):
    return assess_file(parse_diff(_diff(path, *added, context=context)).files[0])


def _codes(assessment) -> set[str]:
    return {f.code for f in assessment.findings}


# Cross site scripting vectors


def test_dangerously_set_inner_html_is_high():
    a = _assess("src/components/Body.tsx", "  <div dangerouslySetInnerHTML={{ __html: body }} />")
    assert a.level is RiskLevel.HIGH
    assert "react-dangerous-html" in _codes(a)


def test_ordinary_jsx_prop_is_not_flagged():
    a = _assess("src/components/Body.tsx", "  <div className={styles.body}>{body}</div>")
    assert a.level is RiskLevel.LOW


def test_inner_html_assignment_is_high():
    a = _assess("src/legacy/mount.ts", "  el.innerHTML = template;")
    assert a.level is RiskLevel.HIGH
    assert "inner-html" in _codes(a)


def test_reading_inner_html_is_not_flagged():
    a = _assess("src/legacy/mount.ts", "  const current = el.innerHTML;")
    assert a.level is RiskLevel.LOW


def test_document_write_is_high():
    a = _assess("src/legacy/boot.ts", "  document.write(snippet);")
    assert a.level is RiskLevel.HIGH


def test_new_function_is_high():
    a = _assess("src/utils/compile.ts", "  const fn = new Function('a', body);")
    assert a.level is RiskLevel.HIGH


# Secrets reaching the browser bundle


def test_public_env_secret_is_high():
    a = _assess("src/config/client.ts", "  const s = process.env.NEXT_PUBLIC_STRIPE_SECRET;")
    assert a.level is RiskLevel.HIGH
    assert "public-env-secret" in _codes(a)


def test_public_env_key_is_only_medium():
    # A publishable key legitimately lives here, so this is a glance.
    a = _assess("src/config/client.ts", "  const k = process.env.NEXT_PUBLIC_STRIPE_KEY;")
    assert a.level is RiskLevel.MEDIUM
    assert "public-env-key" in _codes(a)


def test_ordinary_public_env_var_is_not_flagged():
    a = _assess("src/config/client.ts", "  const url = process.env.NEXT_PUBLIC_API_URL;")
    assert a.level is RiskLevel.LOW


def test_token_in_local_storage_is_high():
    a = _assess("src/state/store.ts", "  localStorage.setItem('authToken', token);")
    assert a.level is RiskLevel.HIGH
    assert "token-in-storage" in _codes(a)


def test_harmless_local_storage_is_not_flagged():
    a = _assess("src/state/store.ts", "  localStorage.setItem('theme', 'dark');")
    assert a.level is RiskLevel.LOW


# Quality signals


def test_ts_ignore_is_medium():
    a = _assess("src/utils/parse.ts", "  // @ts-ignore")
    assert a.level is RiskLevel.MEDIUM
    assert "ts-ignore" in _codes(a)


def test_only_in_a_test_is_medium():
    a = _assess("src/utils/parse.spec.ts", "  it.only('does the thing', () => {")
    assert a.level is RiskLevel.MEDIUM
    assert "test-only" in _codes(a)


def test_normal_test_is_not_flagged():
    a = _assess("src/utils/parse.spec.ts", "  it('does the thing', () => {")
    assert a.level is RiskLevel.LOW


def test_cast_to_any_is_medium():
    a = _assess("src/utils/parse.ts", "  const value = raw as any;")
    assert a.level is RiskLevel.MEDIUM
    assert "cast-any" in _codes(a)


def test_as_any_inside_a_comment_is_not_flagged():
    a = _assess("src/utils/parse.ts", "  // handle this as any other case")
    assert a.level is RiskLevel.LOW


def test_blank_target_without_noopener_is_medium():
    a = _assess("src/components/Link.tsx", '  <a href={url} target="_blank">open</a>')
    assert a.level is RiskLevel.MEDIUM
    assert "blank-target" in _codes(a)


def test_blank_target_with_noopener_is_not_flagged():
    a = _assess(
        "src/components/Link.tsx",
        '  <a href={url} target="_blank" rel="noopener noreferrer">open</a>',
    )
    assert a.level is RiskLevel.LOW


def test_empty_catch_is_medium():
    a = _assess("src/api/fetchUser.ts", "  try { await load(); } catch (e) {}")
    assert a.level is RiskLevel.MEDIUM
    assert "empty-catch" in _codes(a)


def test_catch_with_a_body_is_not_flagged():
    a = _assess("src/api/fetchUser.ts", "  try { await load(); } catch (e) { report(e); }")
    assert a.level is RiskLevel.LOW


def test_plain_http_in_a_comment_is_not_flagged():
    a = _assess("src/config/client.ts", "  // see http://example.com/docs for details")
    assert a.level is RiskLevel.LOW


# File level signals


def test_build_config_change_is_medium():
    a = _assess("next.config.ts", "  reactStrictMode: false,")
    assert a.level is RiskLevel.MEDIUM
    assert "path-build" in _codes(a)


def test_env_file_is_high():
    a = _assess(".env.production", "API_URL=https://example.com", context="# settings")
    assert a.level is RiskLevel.HIGH
    assert "env-file" in _codes(a)


def test_env_example_file_is_not_flagged():
    a = _assess(".env.example", "API_URL=", context="# settings")
    assert "env-file" not in _codes(a)


def test_dependency_change_is_medium_and_line_anchored():
    a = _assess("package.json", '    "left-pad": "^1.3.0",', context='  "dependencies": {')
    assert a.level is RiskLevel.MEDIUM
    dep = [f for f in a.findings if f.code == "dependency-change"]
    assert dep and dep[0].line == 2


def test_version_bump_is_not_reported_as_a_dependency_change():
    a = _assess("package.json", '  "version": "2.1.0",', context="{")
    assert "dependency-change" not in _codes(a)
