# GitLab CI MCP

`gitlab-ci-mcp` is a local Model Context Protocol server for inspecting,
diagnosing, and deliberately operating GitLab CI pipelines. It uses GitLab REST
API v4 and exposes only stdio transport.

The server is read-only by default. Mutation tools are registered only when
`GITLAB_ENABLE_WRITES=true`, and each mutation requires a second call containing
an exact confirmation phrase returned by the first call.

## Requirements

- Python 3.11 or newer
- [`uv`](https://docs.astral.sh/uv/)
- GitLab.com or GitLab Self-Managed 17.10 or newer
- A personal, project, or group access token

Use a token with `read_api` scope for inspection. GitLab requires `api` scope for
the pipeline and job operations exposed by this server. Prefer a project token
when one project is sufficient, and give every token an expiration date.

## Setup

```bash
git clone YOUR_REPOSITORY_URL gitlab-ci-mcp
cd gitlab-ci-mcp
uv sync
cp .env.example .env
```

MCP hosts do not automatically load `.env`. Supply the variables in the host's
server configuration or export them before starting the server:

```bash
export GITLAB_URL="https://gitlab.example.com"
export GITLAB_TOKEN="YOUR_GITLAB_ACCESS_TOKEN"
uv run gitlab-ci-mcp
```

The last command waits for MCP messages on stdin and does not print a startup
banner. Test it interactively with:

```bash
uv run mcp dev src/gitlab_ci_mcp/dev_server.py:mcp
```

The Inspector command additionally requires Node.js and `npx` on `PATH`.

## MCP host configuration

From the repository directory, configure a stdio server using an absolute path:

```json
{
  "mcpServers": {
    "gitlab-ci": {
      "command": "uv",
      "args": [
        "--directory",
        "/absolute/path/to/gitlab-ci-mcp",
        "run",
        "gitlab-ci-mcp"
      ],
      "env": {
        "GITLAB_URL": "https://gitlab.example.com",
        "GITLAB_TOKEN": "YOUR_GITLAB_ACCESS_TOKEN"
      }
    }
  }
}
```

Consult your MCP host's documentation for its configuration file location and
secret-storage options.

## Tools

Read-only tools are always registered:

- `list_pipelines`
- `get_pipeline`
- `diagnose_pipeline`
- `get_job`
- `get_job_log`
- `list_job_artifacts`
- `read_artifact_text`
- `lint_ci_config`

Optional settings are grouped into typed request objects in the generated MCP
schema. For example, `diagnose_pipeline` accepts `project`, an optional
`pipeline_id`, and optional `options` containing `ref` and `log_tail_lines`.

When writes are enabled, these tools are also registered:

- `run_pipeline`
- `retry_pipeline`
- `cancel_pipeline`
- `retry_job`
- `cancel_job`
- `play_manual_job`

### Write confirmation

Set `GITLAB_ENABLE_WRITES=true` in the MCP host configuration and restart the
server. A mutation called without the required confirmation returns a preview:

```json
{
  "ok": false,
  "summary": "Confirmation required; no mutation was performed",
  "confirmation_required": "CANCEL PIPELINE group/project#42",
  "action": "cancel_pipeline"
}
```

Only a subsequent call containing that exact, case-sensitive phrase performs
the operation. The server also reads the current pipeline or job first and
rejects operations that do not match its state.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `GITLAB_URL` | `https://gitlab.com` | GitLab instance root URL |
| `GITLAB_TOKEN` | none | Required GitLab access token |
| `GITLAB_ENABLE_WRITES` | `false` | Register guarded mutation tools |
| `GITLAB_TIMEOUT_SECONDS` | `20` | Timeout for one GitLab HTTP request |
| `GITLAB_MAX_RESPONSE_BYTES` | `2000000` | Maximum accepted API or log response size |
| `GITLAB_MAX_ARTIFACT_BYTES` | `100000` | Maximum text artifact preview size |
| `GITLAB_MAX_LOG_LINES` | `1000` | Maximum lines returned from a job log |
| `GITLAB_REDACT_PATTERNS` | empty | Comma-separated regular expressions to redact |

`GITLAB_MAX_ARTIFACT_BYTES` must not exceed `GITLAB_MAX_RESPONSE_BYTES`.
Invalid URLs, limits, booleans, and redaction expressions stop the server before
it connects to the MCP host.

## Output and errors

Successful tools return `ok`, a short `summary`, and structured `data`. GitLab
failures surface stable error codes such as `unauthorized`, `forbidden`,
`not_found`, `conflict`, `rate_limited`, and `unsupported_version` without
including GitLab response bodies.

Log and artifact redaction removes common GitLab token formats, authorization
headers, URL credentials, common secret assignments, ANSI escapes, and custom
patterns. Redaction is explicitly marked `best_effort`; do not treat it as a
substitute for GitLab masked variables and protected pipeline settings.

Artifact previews accept only relative paths and UTF-8 text. Binary content,
parent traversal, oversized responses, and redirects to a different origin are
rejected. The server does not cache API responses or write artifacts to disk.

## Development

```bash
uv sync --dev
uv run ruff check .
uv run mypy
uv run pytest
uv run python scripts/smoke_stdio.py
```

Normal GitLab failures return `ok: false` with a structured `error`. Callers
should inspect its stable code and either correct access/configuration or honor
`retry_after` for rate limits; they should not retry permission and validation
errors blindly.

## Security

Read [SECURITY.md](SECURITY.md) before enabling writes or exposing logs and
artifacts to an MCP client.
