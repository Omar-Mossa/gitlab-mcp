# Security guide

## Token permissions

The server can access every project visible to `GITLAB_TOKEN`; it does not add a
project allowlist. Prefer a project access token with `read_api` scope. Use the
broader `api` scope only when mutation tools are required. Set an expiration and
rotate the token according to your GitLab policy.

Pass the token through the MCP host's environment or secret store. Do not put it
in command arguments, commit it to the repository, or include it in bug reports.
The server sends it only in GitLab's `PRIVATE-TOKEN` header and does not log HTTP
headers or response bodies.

## Mutation boundary

Write tools have two independent gates:

1. `GITLAB_ENABLE_WRITES=true` must be present when the server starts.
2. Every write call must contain the exact action-specific confirmation phrase
   returned by its preview response.

The confirmation phrase reduces accidental operations but does not replace the
MCP host's own tool-approval controls. Keep those controls enabled. The token's
GitLab role remains the final authorization boundary.

This version cannot delete pipelines, erase jobs, edit CI files, administer
variables, change schedules, or manage runners.

## Logs and artifacts

CI logs and artifacts can contain credentials even when a pipeline was intended
to mask them. The built-in redactor covers common token and assignment shapes,
but arbitrary secrets cannot be detected reliably. Add organization-specific
expressions through `GITLAB_REDACT_PATTERNS`, and test them against representative
logs before using the server with sensitive projects.

Artifact reads are limited to UTF-8 text files and bounded by
`GITLAB_MAX_ARTIFACT_BYTES`. Responses are held only for the tool call and are
not written to disk. Redirects outside the configured `GITLAB_URL` origin are
rejected, including object-storage redirects and HTTPS downgrades.

## Reporting a vulnerability

Do not include tokens, raw job logs, artifact contents, or unredacted GitLab
responses in a report. Revoke any credential that may have been exposed, then
provide the affected server version, GitLab version, tool name, and a sanitized
reproduction.
