# Edge cases

This is the catalog of edge cases Grasp is built to handle. The guiding rule is the same throughout: fail softly, explain clearly, never crash on input a user can produce, and never make a confident claim the tool cannot support. Items marked tested have a direct test in the suite.

## Input and source

1. Patch file does not exist. Clear message, exit code 2. Handled in `diffsource.from_file`. Tested.
2. Path given where a patch file was expected is a directory. Clear message, exit code 2. Handled in `diffsource.from_file`.
3. Binary patch file passed to `--file`. Detected by a null byte, rejected with a message. Handled in `diffsource.from_file`.
4. Empty diff, from any source. Reported as nothing to review, clean exit. Handled in `cli.review`. Tested through the stdin path.
5. More than one source selected, for example `--file` and `--range`. Rejected with a message, exit code 2. Handled in `cli._get_diff_text`. Tested through the CLI.
6. Standard input requested with nothing piped in. Detected and rejected. Handled in `diffsource.from_stdin`.
7. Not inside a git repository when a git source is used. Clear message, exit code 2. Handled in `diffsource._ensure_git_repo`.
8. git not installed. Clear message, exit code 2. Handled in `diffsource._run_git` and `_ensure_git_repo`.
9. Invalid git ref or range. The git error is surfaced in plain words, exit code 2. Handled in `diffsource._run_git`.
10. Very large diff. Over a configurable byte limit, the run asks for confirmation when interactive, or warns and proceeds when not. Handled in `cli.review`.

## Diff parsing

11. New file. Recognized, old path is None. Handled in `diffparser`. Tested.
12. Deleted file. Recognized, new path is None, removed lines counted. Tested.
13. Renamed file, including a pure rename with no hunks. Old and new paths captured from the rename lines. Tested.
14. Copied file. Captured from the copy lines. Handled in `diffparser`.
15. Mode change only, no content. Tagged as a mode change rather than left unknown. Handled in `diffparser._finalize`.
16. Binary file. Detected from the binary marker, no hunks, never sent to the model. Tested.
17. Hunk header without explicit counts, such as `@@ -1 +1 @@`. Counts default to 1. Tested.
18. Malformed hunk header. Skipped and recorded as a warning rather than crashing. Tested.
19. The no newline at end of file marker. Treated as a marker, not content, so counts stay correct. Tested.
20. Multiple files in one diff. Each parsed independently. Tested.
21. Plain, non git unified diff that starts straight at the `---` line. Handled as a single file. Tested.
22. Quoted paths with spaces or special characters. Surrounding quotes stripped. Tested.
23. New file line numbers. Tracked per line so secrets and the model can reference real locations. Tested through secret detection.
24. Commit or email preamble before the first diff, as from format-patch. Ignored until a real file header appears. Handled in `diffparser`.

## Risk triage

25. Sensitive path, such as auth, payments, crypto, the data layer, or infrastructure. Raised to high with a named reason. Tested.
26. Dangerous content in added lines, such as eval, exec, os.system, a shell with shell=True, unsafe deserialization, disabled TLS, or string built SQL. Raised to high. Tested.
27. A secret added in the diff. Raised to high and reported with its line. Tested.
28. A TODO or FIXME, a broad except that swallows errors, or a log of a sensitive value. Raised to medium. Tested.
29. A deleted test file, or removed test assertions. Raised to medium. Tested.
30. A very large change. Raised to at least medium, because size alone makes review unreliable. Tested.
31. A benign change. Reported as low, with an explicit reason that nothing stood out, so silence is never ambiguous. Tested.
32. Duplicate reasons from overlapping rules. De duplicated while keeping order.

## Secrets and privacy

33. Secret present and the model is a cloud API. The run stops before anything is sent, listing the files and lines. Tested.
34. Secret present with `--allow-secrets`. The secret line is masked before sending, and the real value never appears in the prompt. The user is still told about it locally. Tested.
35. Secret present in local mode. Nothing leaves the machine, so the run proceeds and still reports the secret. Tested.
36. A removed line that looks like a secret. Ignored, because removing a secret is good and it was already in history.

## Model and network

37. No model reachable and no key, in auto mode. Clear setup guidance for both local and API, plus a reminder that `--no-model` works. Handled in `config._resolve_mode`.
38. API mode without a key. Clear message to set the environment variable or switch modes. Handled in `config._validate`.
39. Local model selected but not running. The connection error becomes a message telling the user how to start it. Handled in `model._post`.
40. Rate limit or server error from the provider. Retried with increasing waits, then reported in plain words. Handled in `model._post`.
41. A 4xx rejection, such as a bad key. Not retried, the provider message is surfaced. Handled in `model._post`.
42. Timeout. Retried, then reported with a suggestion to try a smaller change. Handled in `model._post`.
43. Reply that is not valid JSON. One repair attempt, then a clear error. Handled in `model.narrate`.
44. Reply that is valid JSON but the wrong shape. Rejected by the schema, repair attempted, then a clear error. Handled in `model._validate`.
45. Reply missing its content field, for either provider shape. Treated as a model error, not a crash. Handled in `model._chat_local` and `_chat_api`.
46. One file fails at the model while others succeed. That file records why its narrative is missing, the rest of the review completes. Tested.

## Output and scope

47. Many files in one change. All are triaged. Only the highest risk files, up to a limit, are sent to the model, and the rest are marked as skipped for the limit. Tested.
48. Binary or no content files reaching the model step. Never sent, marked with the reason. Tested.
49. Writing the report to a path that cannot be written. Clear message, exit code 2. Handled in `cli._emit`.
50. An unknown output format. Rejected with a message. Tested through the CLI.

## Configuration

51. No config file. Fine, defaults are used.
52. Malformed config file. Clear message, exit code 6. Handled in `config._read_config_file`.
53. Unknown keys in the config file. Rejected by name, so typos are caught. Handled in `config._read_config_file`.
54. API key only ever read from the environment, never from a flag or the config file, so it is not written to disk or shell history. Handled in `config.load_settings`.

## Known limits, stated honestly

- The deterministic rules target common languages and shapes. They will miss some risks and will occasionally flag something benign. Every flag is phrased as a point to check, so a wrong flag costs a glance, not a mistake.
- An import that cannot be verified is reported as unverified, never as definitely invented, because it may be a private or internal package.
- The narrative comes from a model and can be wrong. It is grounded in the parsed diff and the questions exist so the human verifies rather than trusts. Grasp is an aid, not an authority.
