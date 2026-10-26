# AWS Diagnostics MCP

Give an AI agent access to live AWS EC2 diagnostics through a reusable Python
MCP server. Built for the Hermes AWS diagnostics project; compatible clients
can use the same stdio server on a desktop or VPS.

**Connect AWS → discover instances → select an instance → diagnose.**

The tool reads instance health, CPU/credit/network metrics, EBS latency, security groups,
public IP, route tables, gateway attachments, network ACLs and SSM configuration. The agent explains findings
using the returned evidence. Normal diagnosis needs no SSH login, key or
source IP. SSH configuration analysis is optional for SSH connectivity issues.

## Install

Requirements: Python 3.11+, [pipx](https://pipx.pypa.io/stable/installation/)
and Git. Once this repository is published, install with:

```sh
pipx install "git+https://github.com/AbdullahXDeveloper/hermes-aws-diagnostics-agent.git@main"
```

Or install from a downloaded/cloned checkout:

```sh
pipx install .
```

If the command is not found after installation, run `pipx ensurepath` and reopen
your terminal. For an AI agent performing installation, follow
[the agent installation guide](docs/AGENT_INSTALL.md).

## Connect your agent

For a client using JSON with an `mcpServers` object, replace the example path
below with your client's actual MCP configuration file:

```sh
aws-diagnostics-mcp setup --no-login --profile diagnostics --config /actual/path/to/client-mcp.json
```

Setup checks AWS CLI v2.32+, installs it for the current user if needed using
[AWS's official installer](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html),
verifies its version and writes MCP configuration. Other entries are preserved;
changed files are backed up. Conflicting settings require `--replace`.
Installation needs network access and permission to install software.

Access defaults to `auto`: a configured `--role-arn` is assumed with a restrictive
STS session policy; otherwise the existing IAM/SSO profile is used directly. Creating
a new role is optional. Direct profiles need diagnostic read permissions themselves.
`--role-arn` can also come from `AWS_DIAGNOSTICS_ROLE_ARN` or an existing client
registration. Setup/configure preserve the existing role and access settings when
no replacement is supplied. Use `--access-mode role` to require a restricted role,
or `--access-mode direct` to explicitly use the source profile. Invalid role ARNs
fail before configuration writes. Explicit role mode without an ARN returns
`security_setup_required`; auto/direct mode can connect without one.

- Add `--region ap-south-1` or your workload region; default is `us-east-1`.
- Add `--remote` when the tool runs on a VPS.
- Omit `--no-login` to sign in interactively from your terminal during setup.
- Add `--no-install` if AWS CLI is managed separately.
- Omit `--config` to print configuration for manual registration.

Reload the client, then ask **Connect AWS**. Sign in and approve directly on
AWS's page. Personal/IAM Console login and configured company SSO profiles are
supported. On VPS, remote Console login requires AWS's one-time authorization
code. See [authentication](docs/AUTHENTICATION.md) for details.

For clients with other config formats, translate the command, args and env from
setup's output into the client's format. The helper writes JSON only. An
installed stdio server can also be launched as:

```sh
aws-diagnostics-mcp serve
```

Example environment/configuration:
[desktop](examples/mcp-config.json), [VPS](examples/mcp-config-vps.json),
[SSO profile](examples/aws-config.example).
Examples assume the executable is on PATH; setup generates an absolute Python
path for clients that do not inherit your terminal's PATH.

## Use

```text
Show my EC2 instances in ap-south-1.
Diagnose the instance I selected.
Explain the findings and their evidence.
```

If login expires, the agent can reconnect and resume a pending diagnosis.
Discovery covers the selected AWS account/profile and one region per call.
Clients must support stdio MCP; individual agent integrations need their own
acceptance test.

| MCP tool | Purpose |
| --- | --- |
| `connect_aws` | Reuse a session or begin AWS browser login |
| `complete_aws_login` | Complete remote Console login with AWS's authorization code |
| `connection_status` | Check login/session status |
| `list_regions` | List enabled AWS regions |
| `discover_instances` | List instances in a region, optionally filtered by name |
| `diagnose_instance` | Collect evidence and produce findings |
| `resume_diagnosis` | Resume a request after reconnecting |

## Access and scope

The server exposes diagnostic read operations only. Access is configurable:

| Mode | Behavior | Additional AWS credential restriction |
| --- | --- | --- |
| `auto` (default) | Assume the configured diagnostics role; use the source profile directly when no role is configured | Restricted STS session only when using a role |
| `role` | Require an existing trusted diagnostics role | Explicit-deny STS read-only session policy |
| `direct` | Use the configured AWS profile's credentials | None added by this server; existing IAM policies control access |

Set the mode with `--access-mode` or `AWS_DIAGNOSTICS_ACCESS_MODE`. A failed role
assumption never falls back to direct access. Role sessions default to one hour;
the [role policy](examples/read-only-policy.json) and trust are configured by the
administrator. In direct mode, use an existing appropriately scoped profile or
grant the reads in [the direct-profile example](examples/direct-read-policy.json).
Direct mode does not remove broader source permissions and returns
`read_only_enforced: false`; role mode returns `true` for the additional STS
restriction. Successful diagnosis reports also include the selected access mode.

Root is blocked by default. To deliberately use a root source, configure
`--access-mode direct --allow-root` (or `AWS_DIAGNOSTICS_ACCESS_MODE=direct` and
`AWS_DIAGNOSTICS_ALLOW_ROOT=true`). This enables only the server's existing
diagnostic operations; the root credentials themselves retain full permissions.
Use `--no-allow-root` to revoke the opt-in. Root cannot assume a diagnostics role.
The server never enables this option automatically.

Follow [IAM/access setup](docs/IAM_SETUP.md) for direct profile permissions, optional
role trust and boundary troubleshooting. Configured roles are assumed automatically;
manual Console role switching is not required.
Passwords and AWS credentials stay in AWS's browser/CLI/SDK flows and local cache.

In role mode, credentials are renewed on each tool operation; their expiry does not require
browser login while the source login remains valid. Console login credentials refresh
automatically for up to 12 hours, subject to AWS session limits. To request a different
role credential lifetime, set `AWS_DIAGNOSTICS_SESSION_DURATION_SECONDS` in the MCP
server environment (900–43200; default 3600). Longer requests require the role's AWS
maximum session duration to permit them. Role chaining, including an assumed SSO role
assuming the diagnostics role, is limited to one hour. See
[session duration](docs/AUTHENTICATION.md#session-duration) for details.

Set `app_checks=true` to inspect existing `CWAgent` disk/RAM/process telemetry tagged
with the selected `InstanceId` dimension. Service health uses configured PID telemetry;
live service manager state remains unknown. Optionally supply exact `log_group` and
`log_stream` to read the latest bounded sample (at most 100 events over 30 minutes).
Logs may contain sensitive information; the caller selects a stream belonging to the
intended instance. No host commands run, and no agents or log collection are installed.

Set `website_url` to check public DNS, the website's TCP port and HTTPS certificate/TLS
from the diagnostics server. No HTTP request is sent. Private/reserved DNS answers are
not probed. If DNS identifies the selected instance IP, source-specific port configuration
is also analyzed; supply `source_ip`, `connection_path` and `client_port` for that path.
CDN/load balancer association and application responses remain unknown.

Offline SSM reports include instance-profile lookup, role policy simulation and endpoint
inventory/security groups. Agent runtime, live authorization and complete endpoint
reachability remain unknown without host evidence. Missing instance profiles or VPC
endpoints alone do not establish the root cause (other credential/egress modes exist).

Each finding includes evidence, confidence and missing checks. Missing/stale telemetry
and access failures are reported as gaps. EBS latency uses matching five-minute sums
of total I/O time divided by operation count; zero-operation periods have no latency value.

SSH analysis
checks IPv4 configuration, not end-to-end reachability; it does not log into an
instance. Missing context/permissions produce incomplete or unknown results.
See [diagnostic scope](docs/AUTHENTICATION.md#scope-and-security).
See [check details and example request](docs/DIAGNOSTIC_CHECKS.md).

## Development and distribution

- [Purpose of every project file](docs/PROJECT_GUIDE.md)
- [Run tests and build](docs/DEVELOPMENT.md)
- [Installation, updates and release steps](docs/DISTRIBUTION.md)
- [Changelog](CHANGELOG.md)
- [MIT license](LICENSE)

The local checkout being prepared for publication does not confirm that the
GitHub repository or a release tag is already public. GitHub/PyPI publication
and the Hermes demo are separate release steps.
