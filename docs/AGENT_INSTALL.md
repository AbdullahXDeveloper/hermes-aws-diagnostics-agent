# Install instructions for an AI agent

Use this repository as a local stdio MCP tool server. You need terminal access
on the same desktop or VPS where your MCP client will run. These instructions
apply once the repository has been published.

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

   For other config formats, run `aws-diagnostics-mcp configure` and translate
   the returned command, arguments and environment into the client's format.
   Run `setup --no-login` first if AWS CLI is missing. Preserve the
   `AWS_DIAGNOSTICS_AWS_CLI` value from setup's output when registering manually.
   Never overwrite a TOML or YAML file using the JSON helper.

4. Reload/reconnect the MCP client, list its tools and confirm that the
   `aws-diagnostics` server exposes `connection_status` and `discover_instances`.
   Do not claim an integration is verified until real MCP calls succeed.
5. Call `connection_status`. If disconnected, call `connect_aws` and present the
   AWS login link. The user signs in and approves in AWS's browser page.
   Company SSO needs a configured IAM Identity Center profile first.
   Never ask for passwords, MFA codes, access keys or session tokens in chat.
   Remote Console login uses a distinct, sensitive browser authorization code;
   prefer private input or the user's local `aws login --remote` terminal flow.
6. After approval, check `connection_status`, discover instances in the user's
   selected region and ask them to select an instance. Call `diagnose_instance`
   with the returned ID and region. If login expired, resume a pending diagnosis
   after reconnecting with its `request_id`.

Normal diagnosis uses AWS APIs and needs no SSH login, SSH key or source IP.
Enable SSH analysis only for an SSH connectivity question. Ask for the relevant
source IPv4/path only then. Do not assume the agent host IP is the user's SSH IP.

Report findings with their evidence and distinguish unknown/missing data from
healthy checks. This tool cannot change AWS resources or inspect OS processes/logs.
Read-only IAM permissions must be enforced in AWS; the example policy is not
automatically deployed. See [authentication and scope](AUTHENTICATION.md).
