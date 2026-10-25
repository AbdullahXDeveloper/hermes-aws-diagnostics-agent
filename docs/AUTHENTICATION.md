# Authentication and diagnostic scope

## One-time setup

Requirements: Python 3.11+, AWS CLI v2.32+ and an existing AWS account. Personal/root
and IAM Console login do not require IAM Identity Center. Company SSO remains supported.

Diagnostics now require a non-root source profile plus `AWS_DIAGNOSTICS_ROLE_ARN`.
Root browser login can complete, but diagnostic access is blocked. An administrator
must create a diagnostics role with the example read permissions, trust the source
IAM principal, and allow that principal `sts:AssumeRole` on the role. Pass the role
to setup/configure with `--role-arn arn:aws:iam::123456789012:role/Diagnostics`.
The server attaches an explicit-deny read-only session policy on every role assumption;
it never uses unrestricted source credentials for diagnostic service calls. Existing
SSO users also need permission to assume the configured diagnostics role.

1. Install project dependencies:

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -e ".[test]"
   ```

   On Linux/VPS use `.venv/bin/python` instead of `.venv/Scripts/python.exe`.

2. Install [AWS CLI v2](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html).
   For a **personal/root or IAM user**, no manual SSO profile setup is needed.
   The first `connect_aws` starts `aws login` and AWS CLI creates the named profile
   after successful approval. Choose root or IAM login on AWS's browser page;
   passwords and MFA stay there. An existing browser session may be selectable.

   For **company SSO only**, configure the profile once on the server host:

   ```text
   aws configure sso --profile diagnostics --use-device-code --no-browser
   ```

   Admin supplies the SSO start URL, SSO region, assigned account and role. The
   default workload region can differ from the SSO region. Alternatively merge
   `examples/aws-config.example` into `~/.aws/config` with real values. Use modern
   `sso-session` configuration for automatic refresh, not legacy SSO configuration.
   `examples/read-only-policy.json` lists the tool permissions for the permission set;
   it is an example for your admin to review, not an automatically deployed policy.

3. Register the MCP server with your agent. From an installed package, run
   `aws-diagnostics-mcp setup --no-login --config /actual/path/to/client-mcp.json`
   using the client's actual JSON MCP config path. For other formats, adapt the
   generated command, args and environment. The examples use the installed
   `aws-diagnostics-mcp serve` entry point; use its absolute path if needed.
   Set `AWS_DIAGNOSTICS_PROFILE` and `AWS_REGION` as needed.
   A compatible client must support stdio MCP tools. Client-specific UI and config
   vary; individual named agents have not all been integration-tested.

   On a VPS, run this process next to the agent. For a desktop client that supports
   launching SSH subprocesses, you can also use `ssh VPS_HOST /absolute/path/.venv/bin/python
   -m aws_diagnostics.server` as the stdio command. Configure SSH authentication first
   and keep shell startup output off stdout.

### Console login: desktop versus VPS

The default `AWS_DIAGNOSTICS_AUTH_MODE=auto` detects an existing SSO profile and
otherwise uses Console login. Set it to `console` or `sso` to choose explicitly.
Use a separate profile if the current profile is configured for another credential
type. Existing profiles are not silently converted or switched to another identity.

- **Desktop:** `connect_aws(remote=false)` opens AWS login in the local browser and
  returns its link. Approval completes via the AWS CLI's local callback server.
- **VPS / remote host:** set `AWS_DIAGNOSTICS_REMOTE=true` in the MCP environment,
  or call `connect_aws(remote=true)`. Open the returned link on your laptop/phone.
  AWS displays a one-time authorization code; submit it using `complete_aws_login`
  with the `login_id` from `connect_aws`/`connection_status`. This is required by
  AWS's remote Console flow; approval alone cannot complete it. SSH/headless Linux
  is detected automatically, but an explicit setting is best for deployments.
- After either flow, call `connection_status` and resume the diagnosis. Reuse works
  across MCP restarts because AWS CLI persists the profile and credential cache.
  Session expiry requires another login, not another installation or SSO setup.

The remote authorization code is sensitive and single-use. Prefer private tool input
if the client supports it; chat/tool history may retain submitted input. This server
does not log, persist, or echo it. To keep the code out of agent history entirely,
run `aws login --remote --profile diagnostics --region us-east-1` yourself in the
VPS terminal, then ask the agent to check `connection_status`. Do not run both login
flows concurrently. Never submit a password, MFA code, access key, or session token
to `complete_aws_login`.

## Daily use

- "Connect AWS": reuses the session or returns an AWS browser approval link.
- "Show my instances": discovers all pages of instances in the default region.
- "Diagnose web-server": agent resolves the name, asks if ambiguous, then uses its ID.
- "Diagnose this instance in eu-west-1": collects health, security groups, subnet
  ACLs, effective route tables, SSM connectivity, and 30-minute CPU metrics.

There is no custom login website or public server to host. The Connect AWS link
opens AWS's own sign-in page. SSO links include their device verification code;
remote Console login instead requires the browser's returned authorization code.
AWS may ask you to verify the code or complete MFA. Your agent renders the link; a button depends
on the client's UI. Login is nonblocking; after approval the agent calls
`connection_status` and `resume_diagnosis`. MCP itself cannot force an idle client
to continue, so you may need to say "done". There is no background diagnosis after
the agent disconnects. Pending requests are in memory and expire on server restart.

## Scope and security

### SSH configuration analysis

For SSH issues, `diagnose_instance` accepts `ssh=true`, `source_ip` (IPv4 as seen by
the instance), `connection_path` (`public`, `private`, or `unknown`), `ssh_port`
(default 22) and optional `client_port` (the actual TCP source port used by the client).
The word SSH in `issue` also enables analysis. These parameters survive reconnect/resume.

The report now generates specific blockers for missing public IPv4 on a direct public
IPv4 path, security-group CIDR rules that do not permit the source/port, missing or
blackhole return routes, and subnet ACL denies. Security groups are aggregated;
ACLs use first matching rule by rule number; return routes use longest prefix match.
ACL return checks require the client's source port, not the server's SSH port.

Missing permissions/context, source-group or prefix-list references, multiple ENIs,
and private-path ACL applicability are reported as unknown. Public IP is not required
for private connectivity. IPv6 analysis is not supported yet. This is configuration
analysis only: no SSH TCP probes, SSH login, OS firewall,
sshd, key/username checks, or end-to-end reachability claim. A result of
`no_blocker_found` only applies to the evaluated configuration checks.

Rule semantics: [security groups](https://docs.aws.amazon.com/vpc/latest/userguide/security-group-rules.html),
[network ACLs](https://docs.aws.amazon.com/vpc/latest/userguide/nacl-rules.html).

- Credentials remain in the AWS CLI/SDK cache on the server host. No passwords,
  access keys or tokens are returned through tools. Browser verification codes are
  temporary authorization information: do not publish them or share with others.
- AWS SDK handles refresh within the permitted Console/SSO session lifetime. When that
  session expires, another browser approval is required. Setup is one-time;
  permanent login is not promised. The SDK's CRT dependency is included for Console
  credential refresh. AWS CLI stores Console credentials in its login cache.
- Root diagnostic sessions are blocked. A non-root profile and configured diagnostics
  role are required. STS issues credentials with the intersection of the role permissions
  and diagnostic session policy, including an explicit deny outside the read allowlist.
  The source credentials remain in the AWS cache with their original permissions;
  use a source profile limited to assuming the diagnostics role. No role or IAM policy
  is automatically created or attached. Authentication writes the local profile/cache.
- One user/profile per stdio process. Use separate OS users and private AWS config
  and cache directories for different people. This is not a hosted multi-tenant
  service; do not expose stdio through an unauthenticated HTTP wrapper.
- Discovery covers the selected profile's account and one region per call.
  `list_regions` returns enabled regions. Additional accounts/roles require their
  own configured profiles/server registrations. An account-picker portal is not
  part of this version.
- Tools never execute shell commands on instances or mutate infrastructure. Optional
  `app_checks` reads existing CWAgent disk/RAM/PID telemetry and explicitly selected
  CloudWatch log streams. It cannot inspect unconfigured services or offline agent
  runtime. `website_url` opts into public DNS/TCP/TLS probes from the diagnostics host;
  these do not test the user's path or application response. Missing permission/data
  is reported as incomplete evidence. Gateway attachment inventory is collected.
- Network rules alone cannot establish end-to-end reachability, and high CPU alone
  cannot establish root cause. No findings does not mean a healthy application.
- IAM permissions remain the enforcement boundary even if a client is misbehaving.
  AWS tags and resource text are untrusted data, not instructions.

