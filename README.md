# Grasp

A reviewer's assistant for pull requests. Grasp reads a change, tells you which files actually deserve your attention and why, and can leave inline comments on the exact lines you approve.

It never runs the code it reads, never stores it, and works fully offline. It is open source, so every one of those claims can be checked.

## The problem

AI writes a large share of code now, so teams open far more pull requests than anyone can carefully read. The bottleneck moved from writing code to reviewing it. A reviewer still has to understand code they did not write, at volume, under time pressure, and the risky parts look exactly like the boring parts.

Grasp attacks that specific moment. It does not try to write code or to replace the reviewer. It answers one question: out of these fifteen files, which two should a human actually open, and why.

## What it looks like

Run it on a change and you get a report grouped by risk, highest first:

```
───────────────────────── Grasp review ─────────────────────────
2 file(s): 1 high risk, 0 medium, 1 low. Overall: high.

HIGH risk
  auth/session.py (modified, +2 -1)
    . touches authentication or sessions
    . disables TLS certificate verification
    . adds something that looks like a secret or key
    ! possible secret on line(s) 12

low risk
  src/utils/parse.ts (modified, +1 -0)
    . no sensitive areas or risky patterns detected
```

Point it at a pull request with `--post` and it asks before anything is published:

```
1/3 HIGH auth/session.py (line 11)
  disables TLS certificate verification
  include this? [y]es / [n]o / [a]ll remaining / [q]uit:
```

Whatever you approve lands on the pull request as an inline comment on that exact line:

> **Grasp:** disables TLS certificate verification
>
> _Worth a look rather than a verdict._

## Why not just ask an AI in your editor?

Because explaining one file is not the problem. Your editor assistant already does that well. The problem is the review gate.

- Different person. An editor assistant helps whoever is writing the code. Grasp helps whoever is reviewing it.
- Different moment. The assistant lives in the editor during authoring. Grasp lives at the review gate, in your terminal or in CI.
- Push, not pull. An assistant waits for you to ask, one file at a time. Grasp runs across the whole change by itself and hands back a ranked answer.
- A decision, not an opinion. Ask an assistant whether something is risky and the answer varies every time. Grasp's risk levels come from explicit rules, so they are identical on every run and you can audit the reasons.

In one line: an assistant answers when you ask about one file. Grasp decides, automatically and the same way every time, which files a human should even open.

## Install

```
pip install -e .
```

Python 3.9 or newer. That installs the `grasp` command.

If your shell cannot find `grasp` afterwards, the script directory is not on your PATH. Either add it, or use `python3 -m grasp.cli` in place of `grasp` everywhere below.

To post comments on pull requests you also need the [GitHub CLI](https://cli.github.com), signed in with `gh auth login`. Grasp has no GitHub credentials of its own and reuses yours.

## Quick start

Review what you are about to commit, with no model and nothing leaving your machine:

```
grasp review --no-model
```

Review a whole branch against main:

```
grasp review --range main...HEAD --no-model
```

Review any pull request in any repo you can access:

```
grasp review --pr 231 --repo owner/repo --no-model
```

## Review a pull request and comment only where you choose

This is the main workflow and the reason Grasp exists.

```
grasp review --pr 231 --repo owner/repo --post
```

What happens, in order:

1. Grasp fetches the pull request diff through the GitHub CLI you are already signed in to.
2. It runs the review and prints it.
3. It walks the findings one at a time, highest risk first, and asks about each one. You answer yes, no, all remaining, or quit.
4. It shows exactly what is about to be posted and asks once more.
5. It posts a single review. Findings tied to a line become inline comments on that line. Findings about the file as a whole go into the review body.

Nothing is posted without your approval. Approve nothing and nothing is posted.

Flags for this workflow:

| Flag | What it does |
| --- | --- |
| `--post` | Enable the approve and publish step. Requires `--pr` and `--repo`. |
| `--approve-all` | Skip the per finding prompt and approve everything. Intended for scripts. |
| `--max-comments N` | Cap inline comments, default 30. Anything over the cap is listed in the review body rather than dropped. |

Design choices behind it:

- One review, not a pile of comments. GitHub groups the inline comments under a single review, so the pull request does not get buried. Several findings on one line are merged into one comment.
- Grasp always posts as a plain comment. It never approves and never requests changes, so it cannot gate your merge on its own judgement.
- Comment bodies contain only the finding message, never the source line. A secret finding is therefore safe to post in public.
- Every comment is validated against the parsed diff before sending, because GitHub rejects an entire review if one comment points at a line outside the diff.

## Other ways to run it

Grasp reviews a diff, and a diff can come from anywhere. No pull request is required.

```
grasp review                                  # your staged change
git diff HEAD | grasp review --stdin          # everything uncommitted
grasp review --range HEAD~1...HEAD            # your last commit
grasp review --range v1.0...v2.0              # between two tags
grasp review --file change.diff               # a saved patch file
grasp review --format md --out review.md      # write Markdown to a file
```

Because Grasp is installed on your machine, you can run it from inside any git project and it reads that project's change:

```
cd ~/path/to/any-project
grasp review --no-model
```

## Adding a model (optional)

Without a model you get the full risk triage, which is a complete and useful result. A model adds narrative on top: a plain summary of each change, the things it does not verify, and questions a reviewer should be able to answer. A model never changes a risk level.

Local and private, nothing leaves your machine:

```
brew install ollama
ollama pull qwen2.5-coder
ollama serve
grasp review --mode local
```

Or an OpenAI compatible API with your own key. The key is read only from the environment, never from a flag or a config file, so it stays out of your shell history and out of version control:

```
export GRASP_API_KEY=your_key_here
grasp review --mode api
```

The default mode is `auto`, which prefers a reachable local model, so privacy is the path of least resistance.

## Automatic reviews in CI

A ready to use GitHub Action lives in `.github/workflows/grasp.yml`. On every pull request it runs Grasp and posts the review as a comment, so the triage is waiting before a reviewer opens a single file.

By default it runs the deterministic triage only, which needs no secret and sends nothing anywhere. To add the model narrative, create a repository secret named `GRASP_API_KEY` and switch to the "with model" line noted in the workflow file.

To use it in another repository, copy that workflow file into that repository's `.github/workflows/` folder and change the install line to pull Grasp from here:

```
pip install "git+https://github.com/YOUR_USERNAME/grasp.git@main"
```

One setting is required in any repo using the Action: Settings, then Actions, then General, then Workflow permissions set to "Read and write permissions." Without it the comment step cannot post.

## What it flags

Grasp is not a linter. ESLint and Ruff already enforce style and catch unused variables, and duplicating them would just train you to ignore the output. A rule belongs here only if a human reviewer should personally stop and think about it.

High risk, pinned to the line where it was found:

| Rule | Why it matters |
| --- | --- |
| `dangerouslySetInnerHTML` | The main cross site scripting vector in React |
| `.innerHTML` or `.outerHTML` assignment | Raw HTML injection outside React |
| `document.write`, `eval`, `new Function`, `exec` | Executes strings as code |
| A secret behind `NEXT_PUBLIC_`, `VITE_`, or `REACT_APP_` | Those prefixes compile into the browser bundle, so the secret ships to every visitor |
| An auth value in `localStorage` or `sessionStorage` | Readable by any cross site scripting bug |
| `verify=False`, `rejectUnauthorized: false` | Disables TLS verification |
| `os.system`, `shell=True`, `pickle.loads`, unsafe `yaml.load` | Server side code execution |
| SQL built by string concatenation | Injection risk |
| Something that looks like a secret or key | Credential in the diff |
| A committed `.env` file | Usually a mistake. Sample and example files are ignored |

Medium risk, worth a glance:

| Rule | Why it matters |
| --- | --- |
| A key or token behind a public env prefix | Might be a publishable key, so confirm rather than panic |
| `@ts-ignore` or `@ts-expect-error` | Type checking switched off on that line |
| `describe.only`, `it.only`, `test.only` | Silently skips the rest of the test suite |
| `as any` | A deliberate hole in the type system |
| `target="_blank"` without `rel="noopener"` | Tabnabbing risk |
| An empty `catch` block, or a broad `except` | Swallows a failure |
| A dependency added or changed in `package.json` | Supply chain |
| Build or compiler config changed | Affects the whole application |
| A removed test assertion, or a deleted test file | Can hide a regression |
| A very large change | Size alone makes review unreliable |
| `TODO` or `FIXME`, or logging something sensitive | Left behind |

File paths that touch authentication, authorization, payments, cryptography, the data layer, or infrastructure are raised on their own, because the area matters even when no single line looks wrong.

Deliberately left out: `key={index}`, exhaustive dependency warnings, unused imports, formatting, plain `console.log`, and bare `: any`. Those are a linter's job or too common to carry signal.

Every rule has a negative test proving the harmless form does not fire. For example `pattern.exec(input)` is a regular expression method, not the dangerous builtin, and reading `el.innerHTML` is not the same as assigning to it. Neither is flagged.

## Tuning the rules for your codebase

None of the above is fixed. What counts as sensitive differs per codebase, so the whole rule set is configurable and no team should have to edit Python. Your money code might live in `/transactions` and someone else's in `/billing`.

See every rule and its code:

```
grasp rules
```

Then put a `.grasp.toml` in your project:

```toml
[rules]
disable = ["todo", "plain-http"]        # silence what is noise for you
large_added = 500                       # move the "too big to review" threshold

[rules.severity]
cast-any = "low"                        # re-grade a rule
dependency-change = "high"

[[rules.path]]                          # your own sensitive areas
code = "path-tenant"
pattern = "(?i)(tenant|organisation)"
message = "touches tenant isolation, which needs a second pair of eyes"
severity = "high"

[[rules.line]]                          # your own line patterns
code = "raw-fetch"
pattern = "\\bfetch\\s*\\("
message = "uses raw fetch instead of the shared api client"
severity = "medium"
unless = "apiClient"                    # but not when the safe form is present
```

The `unless` field is how you express "flag the unsafe form but not the safe one," which is what keeps a custom rule from becoming noise.

An unknown rule code is reported as an error rather than ignored, so a typo cannot silently do nothing. A project level `.grasp.toml` wins over one in your home directory. `.grasp.toml.example` in this repo is a fuller, commented starting point.

## How it works

```
diff -> parse -> risk triage (rules) -> protect secrets -> model narrative -> report -> approve -> post
```

The parse and triage steps are deterministic and need no model. The secret check runs before anything could leave the machine. The model step is optional. The approve step is the only thing that can publish, and only with your consent.

| Module | Responsibility |
| --- | --- |
| `diffsource.py` | Get the diff from staged changes, a range, a file, or stdin |
| `gh.py` | Thin wrapper over the GitHub CLI for diffs, head commits, and posting |
| `diffparser.py` | A defensive unified diff parser |
| `risk.py` | Deterministic rules producing line anchored findings |
| `grounding.py` | Secret and language detection |
| `model.py` | Model client for local or API, with retries and schema validation |
| `review.py` | Orchestration, including secret protection |
| `publish.py` | Build and validate the GitHub review payload |
| `approve.py` | The per finding approval step |
| `report.py` | Terminal and Markdown output |

## Trust and safety

- Your code never leaves your control. Local mode sends nothing. API mode sends straight from your machine to your own provider with your own key.
- The code is never executed. Grasp only reads and parses.
- Nothing is stored. The diff is held in memory for one run and then discarded.
- Secrets are caught before they travel. In API mode a detected secret stops the run, or is redacted with your explicit consent.
- Nothing is published without approval. In a non interactive shell Grasp refuses to guess and stops.
- No telemetry, and no GitHub token of its own.

## Configuration

Everything works with no configuration. An optional `.grasp.toml` in the project or home directory can set defaults:

```toml
mode = "auto"                 # auto, local, or api
local_model = "qwen2.5-coder"
local_endpoint = "http://localhost:11434"
api_model = "gpt-4o-mini"
api_endpoint = "https://api.openai.com/v1"
max_files = 40
```

The API key is never read from this file, only from the `GRASP_API_KEY` environment variable.

## What Grasp does not do

Stated plainly, because a tool that oversells itself is not worth trusting.

- It is not a bug finder or a security scanner. It points at what deserves attention; it does not claim to find every defect.
- The rules target common languages and patterns. They will miss some risks and will occasionally flag something harmless. Every finding is worded as something to check rather than a verdict, so a wrong flag costs you a glance.
- Risk is assessed per file. A risk that only appears when two files interact is not caught today.
- The narrative comes from a model and can be wrong. It is grounded in the parsed diff, and the reviewer questions exist so a human verifies rather than trusts.
- Running it twice posts two reviews. There is no deduplication across runs yet.

The full catalogue of handled edge cases is in `docs/EDGE_CASES.md`.

## Tests

```
pip install -e ".[dev]"
pytest
```

The suite covers the diff parser against awkward real world diffs, the risk rules, secret handling, the review payload, and the approval step. A fake model client means the whole flow is tested with no network and no cost.

## License

MIT. See `LICENSE`.
