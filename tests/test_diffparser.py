"""Tests for the diff parser, with a focus on the awkward cases."""

from __future__ import annotations

from grasp.diffparser import ChangeType, LineKind, parse_diff


def test_simple_modification():
    text = (
        "diff --git a/app.py b/app.py\n"
        "index 111..222 100644\n"
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -1,3 +1,4 @@\n"
        " import os\n"
        "-x = 1\n"
        "+x = 2\n"
        "+y = 3\n"
        " print(x)\n"
    )
    diff = parse_diff(text)
    assert len(diff.files) == 1
    file = diff.files[0]
    assert file.path == "app.py"
    assert file.change_type is ChangeType.MODIFIED
    assert file.added == 2
    assert file.removed == 1
    added = file.added_lines()
    assert [ln.new_lineno for ln in added] == [2, 3]
    assert added[0].text == "x = 2"


def test_new_file():
    text = (
        "diff --git a/new.py b/new.py\n"
        "new file mode 100644\n"
        "index 0000000..abc1234\n"
        "--- /dev/null\n"
        "+++ b/new.py\n"
        "@@ -0,0 +1,2 @@\n"
        "+print('hi')\n"
        "+print('bye')\n"
    )
    diff = parse_diff(text)
    file = diff.files[0]
    assert file.change_type is ChangeType.ADDED
    assert file.old_path is None
    assert file.new_path == "new.py"
    assert file.added == 2


def test_deleted_file():
    text = (
        "diff --git a/gone.py b/gone.py\n"
        "deleted file mode 100644\n"
        "--- a/gone.py\n"
        "+++ /dev/null\n"
        "@@ -1,2 +0,0 @@\n"
        "-a = 1\n"
        "-b = 2\n"
    )
    diff = parse_diff(text)
    file = diff.files[0]
    assert file.change_type is ChangeType.DELETED
    assert file.removed == 2
    assert file.path == "gone.py"


def test_renamed_file():
    text = (
        "diff --git a/old_name.py b/new_name.py\n"
        "similarity index 95%\n"
        "rename from old_name.py\n"
        "rename to new_name.py\n"
    )
    diff = parse_diff(text)
    file = diff.files[0]
    assert file.change_type is ChangeType.RENAMED
    assert file.old_path == "old_name.py"
    assert file.new_path == "new_name.py"
    assert file.similarity == 95


def test_binary_file():
    text = (
        "diff --git a/logo.png b/logo.png\n"
        "index 111..222 100644\n"
        "Binary files a/logo.png and b/logo.png differ\n"
    )
    diff = parse_diff(text)
    file = diff.files[0]
    assert file.is_binary is True
    assert file.change_type is ChangeType.BINARY
    assert file.hunks == []


def test_multiple_files():
    text = (
        "diff --git a/one.py b/one.py\n"
        "--- a/one.py\n"
        "+++ b/one.py\n"
        "@@ -1 +1 @@\n"
        "-a\n"
        "+b\n"
        "diff --git a/two.py b/two.py\n"
        "--- a/two.py\n"
        "+++ b/two.py\n"
        "@@ -1 +1 @@\n"
        "-c\n"
        "+d\n"
    )
    diff = parse_diff(text)
    assert [f.path for f in diff.files] == ["one.py", "two.py"]


def test_no_newline_marker_is_ignored():
    text = (
        "diff --git a/a.txt b/a.txt\n"
        "--- a/a.txt\n"
        "+++ b/a.txt\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "\\ No newline at end of file\n"
        "+new\n"
        "\\ No newline at end of file\n"
    )
    diff = parse_diff(text)
    file = diff.files[0]
    assert file.added == 1
    assert file.removed == 1


def test_hunk_without_counts():
    text = (
        "diff --git a/a.py b/a.py\n"
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -1 +1 @@\n"
        "-x\n"
        "+y\n"
    )
    diff = parse_diff(text)
    hunk = diff.files[0].hunks[0]
    assert hunk.old_count == 1
    assert hunk.new_count == 1


def test_plain_non_git_diff():
    text = (
        "--- a.py\n"
        "+++ b.py\n"
        "@@ -1 +1 @@\n"
        "-x\n"
        "+y\n"
    )
    diff = parse_diff(text)
    assert len(diff.files) == 1
    assert diff.files[0].added == 1


def test_malformed_hunk_header_does_not_crash():
    text = (
        "diff --git a/a.py b/a.py\n"
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ this is not a real header @@\n"
        "+x\n"
    )
    diff = parse_diff(text)
    assert diff.warnings
    # The malformed hunk is skipped, so no hunks were recorded.
    assert diff.files[0].hunks == []


def test_empty_input():
    diff = parse_diff("")
    assert diff.files == []


def test_quoted_path_with_space():
    text = (
        'diff --git "a/my file.py" "b/my file.py"\n'
        '--- "a/my file.py"\n'
        '+++ "b/my file.py"\n'
        "@@ -1 +1 @@\n"
        "-x\n"
        "+y\n"
    )
    diff = parse_diff(text)
    assert diff.files[0].path == "my file.py"
