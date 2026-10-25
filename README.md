# AWS Diagnostics MCP

Give an AI agent access to live AWS EC2 diagnostics through a reusable Python
MCP server. Built for the Hermes AWS diagnostics project; compatible clients
can use the same stdio server on a desktop or VPS.

**Connect AWS → discover instances → select an instance → diagnose.**

The tool reads instance health, CPU metrics, security groups, public IP,
route tables, network ACLs and SSM connectivity. The agent explains findings
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
aws-diagnostics-mcp setup --no-login --config /actual/path/to/client-mcp.json
```

Setup checks AWS CLI v2.32+, installs it for the current user if needed using
[AWS's official installer](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html),
verifies its version and writes MCP configuration. Other entries are preserved;
changed files are backed up. Conflicting settings require `--replace`.
Installation needs network access and permission to install software.

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

Tool operations read AWS data and do not modify infrastructure. Enforce access
with a dedicated read-only IAM identity/role; the
[example permissions policy](examples/read-only-policy.json) is not deployed
automatically. Root login is supported but its underlying permissions are broad.
Passwords and AWS credentials stay in AWS's browser/CLI/SDK flows and local cache.

No OS processes, disk usage or application logs are inspected. SSH analysis
checks IPv4 configuration, not end-to-end reachability; it does not log into an
instance. Missing context/permissions produce incomplete or unknown results.
See [diagnostic scope](docs/AUTHENTICATION.md#scope-and-security).

## Development and distribution

- [Run tests and build](docs/DEVELOPMENT.md)
- [Installation, updates and release steps](docs/DISTRIBUTION.md)
- [Changelog](CHANGELOG.md)
- [MIT license](LICENSE)

The local checkout being prepared for publication does not confirm that the
GitHub repository or a release tag is already public. GitHub/PyPI publication
and the Hermes demo are separate release steps.
