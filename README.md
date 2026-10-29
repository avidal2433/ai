# ai

Agent skills packaged as plugins for **Claude Code**, **Codex** and **opencode**, installable from a single marketplace (`avidal`).

> The skills' instructions are written in Spanish.

## Plugins

| Plugin | What it does |
| --- | --- |
| [`bitbucket-code-review`](plugins/bitbucket-code-review) | Reviews a Bitbucket pull request against the repository code and its linked ClickUp task, verifies each finding independently, publishes the confirmed ones as PR comments and requests changes, or approves the pull request when the review comes out clean and its end-to-end tests pass, when you authorize it. |

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
- `ffmpeg` and `ffprobe`, to build the MP4 that records the end-to-end tests.

### Environment variables

| Variable | Required | Purpose |
| --- | --- | --- |
| `BITBUCKET_EMAIL` | Yes | Atlassian account email used for Bitbucket API basic auth. |
| `BITBUCKET_API_TOKEN` | Yes | Atlassian API token with read access to repositories and pull requests (and write access to publish comments, request changes and approve). |
| `CLICKUP_API_KEY` | No | Adds the linked ClickUp task (and its parent) as review context, and leaves the end-to-end video and a short explanation of the cases tested on the tasks the pull request links (needs write access). Without it, the review continues and reports the limitation. |
| `CLICKUP_WORKSPACE_ID` | No | Only needed to resolve ClickUp custom task IDs such as `ABC-123`. |
| `BITBUCKET_WORKSPACE` | No | Default workspace when the PR is given by number instead of URL. |
| `BITBUCKET_ACCOUNT_ID` | No | Account ID of the token owner, for tokens that cannot read `GET /user`. Without an identity, re-reviews cover the whole PR and the skill cannot resolve threads, withdraw its change requests or approve. |

**Codex:** commands run with a filtered environment (`shell_environment_policy`) inside a sandbox. Make sure the variables above reach the shell Codex uses and that network access to `api.bitbucket.org` and `api.clickup.com` is allowed.

### Usage

Ask the agent to review a pull request, for example:

```
Revisa este PR https://bitbucket.org/<workspace>/<repo>/pull-requests/123
```

When you authorize publishing, the skill posts its findings and then requests changes on the pull request. Findings include any explicit acceptance criterion of the linked task that the pull request does not meet. Every pending finding stays visible: if the author resolves one of the skill's threads while the defect is still there, a re-review reopens it and replies with what is missing, because a reply alone does not reopen a resolved thread. When a review posts nothing and merging needs nothing outside the pull request (full coverage, CI without failed or running checks, no coordinated deploys, migrations elsewhere or branch-pinned dependencies), it runs end-to-end tests on the reviewed commit before approving: the change and the flows around it, including the apps that consume it, through their real entry point (browser, HTTP, command) in an environment of its own, plus the automated suite. If they all pass, it approves the reviewed commit without posting a comment: the approval itself is the confirmation. A scenario it could not run blocks the approval. Every end-to-end run is recorded as one MP4. Before the review state is set, the video and a short explanation of the cases tested, written for the client, go on each ClickUp task the pull request description links: the requirement tasks shared with the client. When it finds nothing but cannot approve, such as a dependency outside the pull request it could not confirm, it posts one comment naming the missing condition so the author can settle it with the reviewer. A later review replaces or withdraws that change request or approval. A change request is withdrawn only if the skill set it, so one you set by hand stays in place; approvals carry no marker, so any approval from the reviewer account counts as the skill's and a later review with findings withdraws it. A re-review that posts new findings renews the skill's change request, so a fresh request and its marker comment come after those findings. It never declines or merges pull requests.

## Adding a plugin

1. Create `plugins/<name>/.claude-plugin/plugin.json` and put its skills under `plugins/<name>/skills/<skill>/SKILL.md`.
2. Add an entry to `.claude-plugin/marketplace.json` with `"source": "./plugins/<name>"`.
3. Validate with `claude plugin validate .` and `claude plugin validate plugins/<name>`.
4. Bump `version` in the plugin's `plugin.json` on every release: Claude Code uses it to detect updates.

## License

[MIT](LICENSE)
