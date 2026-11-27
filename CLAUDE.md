# CLAUDE.md

All repository guidance lives in [AGENTS.md](AGENTS.md) (shared with other agents) — follow it:

@AGENTS.md

## Claude Code specifics

- The `otii` MCP server is registered in [`.mcp.json`](.mcp.json) (`.venv/bin/otii-usb-mcp`); prefer its
  tools (`status`, `measure`, `record`) over shelling out when you only need readings.
- `set_output` and `power_cycle` switch power to real hardware: confirm with the user before
  calling them unless they explicitly asked for that action in the current request.
- Run `python -m pytest` after every code change.
- Do not add Co-Authored-By or other assistant attribution to commit messages or source files;
  the only such mention is the line in the README's License section.
- Keep [AGENTS.md](AGENTS.md) the single source of truth: put repo-wide changes there, and only
  Claude-specific notes here.
