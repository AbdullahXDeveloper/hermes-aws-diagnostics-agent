# Changelog

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
