# Install instructions for an AI agent

Use this repository as a local stdio MCP tool server. You need terminal access
on the same desktop or VPS where your MCP client will run. These instructions
apply once the repository has been published.

## Quick request for users

Send your AI agent this message with a link to this guide:

> Install this tool: https://github.com/AbdullahXDeveloper/hermes-aws-diagnostics-agent/blob/main/docs/AGENT_INSTALL.md

The agent needs terminal access and a client that supports local stdio MCP
servers. The repository must be accessible to that agent. AWS browser login
and approval remain actions for you to complete.

## Default setup behavior for agents

When the user asks to install, set up, connect or add this tool and supplies
this guide, treat a short or misspelled request as a request to complete the
installation workflow below: install prerequisites and the package, register
the MCP server in the intended client, and verify actual MCP tool calls.
Do not stop after downloading the repository or printing commands when you
have the access and authorization to perform the setup.

Respect explicit user limits, the client's permissions and any required
software-installation approvals. This guide does not grant additional access.
If the user only asks a question about the guide, answer it; a link alone is
not authorization to install software. If the intended client cannot be
identified from context, ask which client to configure before editing its
settings. Do not require the user to supply a configuration path that you can
discover from the client's documented configuration.

Reuse a suitable existing installation and server registration when present.
Preserve other MCP entries and inspect conflicts before changing them. If a
client reload or user action is required, finish the available setup first,
then explain the exact remaining step. Report separately whether the package
is installed, the server is registered, MCP calls are verified, and AWS is
connected. Do not report success for steps you could not verify.

After MCP verification, guide the user through AWS login if requested or needed
for their requested AWS task. Installation alone does not require starting a
diagnosis: discover instances and diagnose only when the user requests that
work and selects the target instance.

## Installation and verification steps

1. Check for Python 3.11+, pipx and Git. Install missing prerequisites using your
   platform's supported installation method and the user's software permissions.
2. Install the package in its own environment:

   ```sh
   pipx install "git+https://github.com/AbdullahXDeveloper/hermes-aws-diagnostics-agent.git@main"
   ```

   From a downloaded/cloned checkout, use `pipx install .` instead.
   If the pipx executable directory is not on PATH, use its absolute executable
   path or run `pipx ensurepath` and reopen the terminal.

3. Find the actual MCP configuration location and format for the active client.
   Do not guess a path. For JSON clients using an `mcpServers` object, run:

   ```sh
   aws-diagnostics-mcp setup --no-login --config /actual/path/to/client-mcp.json
   ```

   The path above is a placeholder: replace it with the client's real config file.
   Add `--region ap-south-1` (or the user's region), `--profile diagnostics` and,
   for VPS, `--remote`. Setup downloads and runs AWS's official current-user
   installer if AWS CLI v2.32+ is unavailable. Use `--no-install` if software
   installation is managed separately. Do not automatically add `--replace`:
   inspect conflicting settings before replacing them. Other JSON entries are
   preserved and an existing changed config is backed up.

   Add `--role-arn` with the user's existing trusted diagnostics role ARN when
   available. Diagnostics require a non-root source profile and restricted STS
   role session. If the role is not provisioned yet, finish local installation
   and report `security_setup_required`; do not create IAM resources or weaken
   the boundary as part of installation. The example read policy is for the
   account administrator to deploy.
   Follow [one-time IAM setup](IAM_SETUP.md) when the user explicitly requests IAM
   provisioning. Normal installation alone does not authorize creating IAM resources.

   For other config formats, run `aws-diagnostics-mcp configure` and translate
   the returned command, arguments and environment into the client's format.
   Run `setup --no-login` first if AWS CLI is missing. Preserve the
   `AWS_DIAGNOSTICS_AWS_CLI` value from setup's output when registering manually.
   Never overwrite a TOML or YAML file using the JSON helper.

4. Reload/reconnect the MCP client, list its tools and confirm that the
   `aws-diagnostics` server exposes `connection_status` and `discover_instances`.
   Do not claim an integration is verified until real MCP calls succeed.
5. Call `connection_status`. If disconnected and AWS access is requested, call `connect_aws` and present the
   AWS login link. The user signs in and approves in AWS's browser page.
   Company SSO needs a configured IAM Identity Center profile first.
   Never ask for passwords, MFA codes, access keys or session tokens in chat.
   Remote Console login uses a distinct, sensitive browser authorization code;
   prefer private input or the user's local `aws login --remote` terminal flow.
   A `security_setup_required` response needs the non-root profile/role configuration,
   not another browser login. Role trust and `sts:AssumeRole` permission must be in place.
   IAM Console login also requires `SignInLocalDevelopmentAccess` on the source
   identity. Check existing permissions boundaries if AWS reports a boundary denial.
6. For a requested diagnosis, after approval, check `connection_status`, discover instances in the user's
   selected region and ask them to select an instance. Call `diagnose_instance`
   with the returned ID and region. If login expired, resume a pending diagnosis
   after reconnecting with its `request_id`.

Normal diagnosis uses AWS APIs and needs no SSH login, SSH key or source IP.
Enable SSH analysis only for an SSH connectivity question. Ask for the relevant
source IPv4/path only then. Do not assume the agent host IP is the user's SSH IP.

Report findings with their evidence and distinguish unknown/missing data from
healthy checks. Optional `app_checks` reads existing CWAgent metrics and selected
CloudWatch log streams; it does not run host commands. Optional `website_url`
checks public DNS/TCP/TLS from the server; source-specific website rule analysis
also needs the relevant source IPv4/path. See [diagnostic checks](DIAGNOSTIC_CHECKS.md).
Diagnostic credentials use an AWS-enforced read-only STS session policy; the
role's permissions/trust are not automatically deployed. See [authentication and scope](AUTHENTICATION.md).
