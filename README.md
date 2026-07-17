# Agent Delegation

`agent-delegation` is a machine-global delegation tool for Claude, Codex/ChatGPT,
and Gemini/agy. It sends bounded search, research, and large-output work to an
isolated worker so the caller keeps a smaller context.

## Safety model

- One delegation level. Every worker receives `AGENT_DELEGATION_DEPTH=1`, and
  another delegation attempt is rejected.
- agy runs with `--sandbox --mode plan`.
- Gemini CLI runs with `--sandbox --approval-mode plan`.
- Permission bypass options are never used.
- Terminal commands are denied unless their token prefix is present in the
  managed global policy or explicitly added by the caller for one run.
- Native agy transcripts and continuation state are never changed or removed.

## Install

From a source checkout:

```powershell
uv tool install --reinstall .
agent-delegation install --force
agent-delegation status
```

The managed home is `~/.agent-delegation/`. User extensions belong in
`~/.agent-delegation/policy.local.json`; setup refreshes `policy.json` without
overwriting the local file.

## Use

```powershell
agent-delegation run "map the test suite" --profile scout --workspace .
agent-delegation run "run the approved test subset" --allow-command "python -m pytest" --workspace .
```

The second example grants one exact command prefix for that run. Shells,
redirection, pipelines, compound commands, delegation commands, and destructive
file commands remain permanently denied.

Disable or enable delegation in one Git repository without adding tracked files:

```powershell
agent-delegation disable .
agent-delegation enable .
```

The setting is stored in the repository's local Git config.

## Logs

Each run creates:

```text
~/.agent-delegation/runs/<caller>/<project>/<run-id>/
  exchange.jsonl
  manifest.json
  native/<agy-conversation-id>/transcript_full.jsonl
```

Native JSONL is copied byte for byte and hash-verified after the child exits.
The original remains in agy's native store. The manifest records caller,
workspace, backend, capability additions, exit code, and hashes.

## Legacy local installations

The old `gemini-delegate` command and per-repository installer remain only as
temporary compatibility interfaces. New setup does not create `.gemini-delegation`
or repository hook copies. Remove old local installs separately after verifying
the global tool.

## Development

```powershell
python -m unittest discover -s tests -v
python -m py_compile src\agent_delegation\*.py src\gemini_delegation\*.py
```
