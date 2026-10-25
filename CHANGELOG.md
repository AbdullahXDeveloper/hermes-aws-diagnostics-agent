# Changelog

## 1.0.0

- Require a non-root source profile and diagnostics role. Assume a short-lived STS
  session with an explicit deny for every operation outside diagnostic reads;
  add `--role-arn` to setup/configure. Existing root-only configurations need migration.
- Collect burst CPU credits, network bytes/packets and per-volume EBS latency.
- Add optional existing CWAgent disk/RAM/process telemetry and bounded, explicitly
  selected CloudWatch log streams without running host commands.
- Inspect offline SSM instance profiles, simulated role permissions and endpoint
  state/private DNS/security groups; distinguish these from unknown live agent state.
- Check internet gateway attachments and optional website DNS/TCP/TLS from the
  diagnostics server, with source-specific port rule analysis when DNS matches the instance.
- Include confidence and missing checks with every finding; preserve new options
  across reconnect/resume and report telemetry gaps explicitly.

## 0.3.0

- Add a setup command that checks AWS CLI, installs it for the current user,
  prepares MCP configuration and starts AWS login; support deferred login for agents.
- Save the AWS CLI executable path so MCP clients can find a new installation
  without restarting the parent terminal.
- Fix inherited PowerShell module paths that prevent the Windows AWS installer
  from verifying its Authenticode signature.
- Add IPv4 SSH configuration findings for security groups, public IP, return
  routes and subnet ACLs, with unknown results for incomplete evidence.
- Add MIT licensing, repository metadata and installation instructions for agents.

## 0.2.0

- Package the reusable stdio MCP server, configuration helper and prerequisite check.
- Support Console/SSO login, EC2 discovery and AWS API diagnostics with reconnect/resume.
