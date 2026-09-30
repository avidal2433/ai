# ai

Agent skills packaged as plugins for **Claude Code**, **Codex** and **opencode**, installable from a single marketplace (`avidal`).

> The skills' instructions are written in Spanish.

## Plugins

| Plugin | What it does |
| --- | --- |
| [`bitbucket-code-review`](plugins/bitbucket-code-review) | Reviews a Bitbucket pull request against the repository code and its linked ClickUp task, verifies each finding independently, and publishes the confirmed ones as PR comments when you authorize it. |

## Install

**Claude Code**

```
/plugin marketplace add avidal2433/ai
/plugin install bitbucket-code-review@avidal
```

**Codex**

```bash
codex plugin marketplace add avidal2433/ai
codex plugin add bitbucket-code-review@avidal
```

**opencode** (and other agents supported by the [`skills`](https://github.com/vercel-labs/skills) CLI)

```bash
npx skills add avidal2433/ai --skill bitbucket-code-review --agent opencode --global
```

### Update

| Tool | Command |
| --- | --- |
| Claude Code | `/plugin marketplace update avidal` |
| Codex | `codex plugin marketplace upgrade` |
| opencode | `npx skills update` |

## bitbucket-code-review

### Requirements

- `git`, `curl`, `jq` and `python3` on the `PATH`.
- A local clone of the repository under review, so the code at the PR's source commit can be read.

### Environment variables

| Variable | Required | Purpose |
| --- | --- | --- |
| `BITBUCKET_EMAIL` | Yes | Atlassian account email used for Bitbucket API basic auth. |
| `BITBUCKET_API_TOKEN` | Yes | Atlassian API token with read access to repositories and pull requests (and write access to publish comments). |
| `CLICKUP_API_KEY` | No | Adds the linked ClickUp task (and its parent) as review context. Without it, the review continues and reports the limitation. |
| `CLICKUP_WORKSPACE_ID` | No | Only needed to resolve ClickUp custom task IDs such as `ABC-123`. |
| `BITBUCKET_WORKSPACE` | No | Default workspace when the PR is given by number instead of URL. |

**Codex:** commands run with a filtered environment (`shell_environment_policy`) inside a sandbox. Make sure the variables above reach the shell Codex uses and that network access to `api.bitbucket.org` and `api.clickup.com` is allowed.

### Usage

Ask the agent to review a pull request, for example:

```
Revisa este PR https://bitbucket.org/<workspace>/<repo>/pull-requests/123
```

The skill does not approve, decline or merge pull requests. It only publishes comments after you authorize it.

## Adding a plugin

1. Create `plugins/<name>/.claude-plugin/plugin.json` and put its skills under `plugins/<name>/skills/<skill>/SKILL.md`.
2. Add an entry to `.claude-plugin/marketplace.json` with `"source": "./plugins/<name>"`.
3. Validate with `claude plugin validate .` and `claude plugin validate plugins/<name>`.
4. Bump `version` in the plugin's `plugin.json` on every release: Claude Code uses it to detect updates.

## License

[MIT](LICENSE)
