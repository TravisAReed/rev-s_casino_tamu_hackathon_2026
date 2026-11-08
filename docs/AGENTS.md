# Wiring a coding agent (Grok Build / OpenCode)

**The runner needs no AI.** It is fully deterministic: L1–L5 messages are fixed
strings and L6 is a numeric guess loop. There is no model call in the decision
path, so no API key or Grok Build / OpenCode instance is required for it to run.

If you want your agent to **operate or watch** the runner:

1. Give the agent this repo and let it run the commands in the README (browser
   start, sign-in check, `team_runner.py`).
2. Have it tail `runs/team-runner.log` and `runs/leads/L6-guess.jsonl` to report
   progress and stop or resume around the daily limit.
3. If `mcp_server.py` is present, it exposes the harness over MCP so an agent can
   drive the browser actions directly. It needs no secrets; do not add API keys to
   the repo.

Keep all credentials out of the repo. The only sign-in is the manual Google login
in the browser window.
