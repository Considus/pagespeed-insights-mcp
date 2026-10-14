---
summary: How a coding agent builds, tests and proposes a change to the PageSpeed Insights MCP server.
type: agent-guide
owner: claude
---

# Agent notes

For a coding agent working in this repo. People start with [`CONTRIBUTING.md`](CONTRIBUTING.md); read it before writing, because this file does not repeat it.

## Build

- **Keep the three ground rules in `CONTRIBUTING.md`:** standard library only, no number without its uncertainty, lab and field kept separate. They are constraints, not preferences: a change that breaks one is rejected however good the rest is.
- **Keep Python 3.9 working.** CI runs 3.9 and 3.12 on Linux, macOS and Windows. A 3.10-only construct (`match`, a runtime-evaluated `X | Y` annotation, `zip(strict=)`, `itertools.pairwise`, `tomllib`, `dataclass(slots=)`) passes on a newer interpreter and fails only in the 3.9 leg.
- **Change the version in `pagespeed_insights/__init__.py` and the tool list in the package, then run `./build-mcpb.py --sync`.** `manifest.json` is generated from them, so edit it only through `--sync`. Tool titles and hints are in `CONTRIBUTING.md`, under "Building the bundle".
- **Extract shared logic only when two callers need it.** One caller makes a layer for nothing.

## Test

```bash
python3 -m compileall -q pagespeed_insights setup.py mcp_server.py tests
python3 -m unittest discover -s tests -v
./build-mcpb.py --check
```

A PR needs all three green. Read the test **count**, not the word "passed": a collection error reports zero failures. No test needs an API key or a network connection; to try the real API, follow "Testing against the real APIs" in `CONTRIBUTING.md`.

Capture the **before** while you are still reproducing the problem, and the **after** once the change works. Here that is usually a pair of command outputs.

**Not checkable locally:** the Windows and Linux CI legs, the 3.9 leg unless 3.9 is installed, how a client renders the tool list, and whether the MCP Registry accepts a listing (`mcp-publisher validate` answers that before a release).

## Ship

1. Switch on the committed hooks once per clone: `git config core.hooksPath hooks`. `hooks/pre-commit` refuses a commit on `main`; `hooks/commit-msg` refuses a Claude attribution footer.
2. Check nobody is already on the work, then branch from `origin/main` by name, not from wherever `HEAD` is:

   ```bash
   git fetch origin
   gh pr list -R Considus/pagespeed-insights-mcp
   git checkout -b <type>/<short-name> origin/main
   ```

   If an open PR touches the same files, build on it, or say so and stop.
3. Stage by explicit path, not with `git add -A`.
4. Open the PR against `main` and fill [`.github/pull_request_template.md`](.github/pull_request_template.md): what changed, how it was tested and the risks, with the before and after as evidence.
