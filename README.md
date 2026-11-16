# Grasp

A reviewer's assistant for pull requests. Grasp helps a human understand and triage a change before it is merged.

AI now writes a large share of code, and teams are shipping changes faster than anyone can review them. Review has become the bottleneck, because a human still has to understand code they did not write, at volume. Grasp attacks that bottleneck. It reads a diff, scores the risk of every file with deterministic rules, and, when a model is available, adds a plain explanation and the specific points a reviewer should check.

Grasp never runs the code it reads. It never stores the code. It can run fully offline. It is open source so every one of these claims can be checked.

## Why not just ask an AI in your editor?

Because the problem is not explaining one file. Your editor assistant already does that well. The problem is the review gate, where someone has to get through a whole pull request of code they did not write, at volume, fast.

- Different person. An editor assistant helps whoever is writing the code. Grasp helps whoever is reviewing it.
- Different moment. The assistant lives in the editor during authoring. Grasp lives at the review gate, in your terminal or in CI.
- Push, not pull. An assistant waits for you to ask, file by file. Grasp runs by itself across the whole change and hands back a ranked answer: out of these fifteen files, these two matter, and here is why.
- A decision, not an opinion. Ask an assistant "is this risky" and you get a different answer every time. Grasp's risk level comes from explicit rules, so it is the same every run and you can audit the reasons.

In one line: an assistant answers when you ask about one file. Grasp decides, automatically and the same way every time, which files in a pull request a human should even open.

## What makes it different

1. Risk triage is deterministic, not guessed. The risk level of a file comes from explicit rules over the real diff, so it is reliable and every level comes with its reasons. The model never sets the risk level.
2. It works without a model. With no model configured, you still get the full risk triage. The model only adds narrative on top.
3. It is local first. Your code never leaves your machine unless you choose an API model with your own key, and even then secrets are caught before anything is sent.

## Install

```
pip install -e .
```

This installs the `grasp` command. Python 3.9 or later. On Python 3.9 and 3.10, a small `tomli` package is installed automatically to read the optional config file; on 3.11 and newer the standard library is used.

For narrative, Grasp needs a model. Either run a local one (for example Ollama) so nothing leaves your machine, or set an API key for an OpenAI compatible endpoint:

```
export GRASP_API_KEY=your_key_here
```

## Use

Review the change you are about to commit:

```
grasp review
```

Review a pull request range:

```
grasp review --range main...HEAD
```

Review a patch file, or a diff piped in:

```
grasp review --file change.diff
git diff | grasp review --stdin
```

Risk triage only, no model, fully offline:

```
grasp review --no-model
```

Write a report to paste into a pull request:

```
grasp review --range main...HEAD --format md --out review.md
```

Options worth knowing:

- `--mode auto|local|api` chooses the model. Auto prefers a reachable local model, so privacy is the default path.
- `--no-model` runs the deterministic triage only.
- `--allow-secrets` redacts detected secrets and continues in API mode. Without it, Grasp refuses to send a detected secret to a cloud model.
- `--max-files N` caps how many files are sent to the model. All files are still triaged.

## What you get

A report grouped by risk, highest first. For each file: its risk level and the exact reasons, any detected secret, and, when the model ran, a plain summary, the things the change does not verify, and a few questions the reviewer should be able to answer.

## Use Grasp in your own repositories

Once Grasp is installed, you can run it from inside any git project on your machine. It reads that project's change, not Grasp's.

```
cd ~/path/to/your-project
grasp review                      # review what you are about to commit
grasp review --range main...HEAD  # review a whole branch against main
```

To make Grasp available in a project's CI on GitHub, install it from this repository rather than from the project itself:

```
pip install "git+https://github.com/YOUR_USERNAME/grasp.git@main"
```

## Automatic review on pull requests

A ready to use GitHub Action lives in `.github/workflows/grasp.yml`. On every pull request it runs Grasp and posts the review as a comment, so the triage is waiting for the reviewer before they open a single file.

By default it runs the deterministic triage only, which needs no secret and sends nothing anywhere. To also get the model written narrative, add a repository secret named `GRASP_API_KEY` and switch to the "with model" line noted in the workflow file.

To use the Action in another repository, copy that workflow file into that repository's `.github/workflows/` folder and change the install line to the `git+https` form above.

## How it works

```
diff  ->  parse  ->  risk triage (rules)  ->  protect secrets  ->  model narrative  ->  report
```

The parse and risk steps are deterministic and need no model. The secret step runs before anything is sent anywhere. The model step is optional and never changes a risk level.

## Trust and safety

- The code never leaves your control. Local mode sends nothing. API mode sends straight from your machine to your own provider with your own key.
- The code is never executed. Grasp only reads and parses.
- Nothing is stored. The diff is held in memory for one run and discarded.
- Secrets are caught before they travel. In API mode a detected secret stops the run, or is redacted with your consent.
- No telemetry.

## Edge cases

Grasp is built to fail softly and never crash on bad input. The full catalog of handled cases is in `docs/EDGE_CASES.md`.

## Tests

```
pip install -e ".[dev]"
pytest
```

## License

MIT. See `LICENSE`.
