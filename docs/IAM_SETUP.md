# AWS access setup: direct profiles or an optional diagnostics role

An account administrator provisions any required permissions. The MCP server does not
create users, roles, permissions or credentials. Use your own account ID and
principal ARN in place of every example below; no account is built into the code.

The optional restricted-role flow is: non-root Console/SSO profile -> assume `Diagnostics` ->
read diagnostic evidence. The user approves browser login; the agent assumes the
configured role automatically. Manual Console role switching is only an optional
verification step, not part of each diagnosis.

## Direct access: no new role required

By default, `--access-mode auto` uses the configured role if present; otherwise it
uses the source profile directly. An existing IAM or SSO profile can therefore
work without creating another role. The profile must already allow the required
diagnostic reads. Administrators can grant the reads in
[`direct-read-policy.json`](../examples/direct-read-policy.json) to an appropriate
source identity/SSO permission set. That example grants reads; it does not remove
permissions granted by other policies.

```sh
aws-diagnostics-mcp setup --no-login --profile diagnostics --access-mode auto --config /actual/path/to/client-mcp.json
```

Direct mode does not attach an additional STS session restriction. Status and
successful diagnosis responses report `read_only_enforced: false` even though
the MCP server implements diagnostic reads only. Existing IAM policies still
control what the profile can access, and missing reads are reported as gaps/errors.

For a deliberately selected root profile, use `--access-mode direct --allow-root`.
Root is blocked without that explicit installation setting, and its credentials
retain full permissions. A root source cannot be used to assume the optional role.
Use `--no-allow-root` to disable the setting again.

The remaining role/trust steps are needed only if choosing restricted-role access.

## Source identity

Reuse a suitable non-root IAM identity or the organization's existing SSO role.
For an IAM Console user, configure Console access and MFA through the account's
normal process. Passwords and MFA are entered only on AWS's sign-in pages.
Access keys are not needed for this browser-login flow.

For Console-to-CLI login, attach AWS's managed policy
`SignInLocalDevelopmentAccess` to the source user/group/role. This permits
`signin:AuthorizeOAuth2Access` and `signin:CreateOAuth2Token`; it does not grant
EC2 access. Existing SSO profiles use their organization's SSO authorization.

For restricted-role access, grant the source identity permission to assume only the diagnostics role:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": "sts:AssumeRole",
    "Resource": "arn:aws:iam::123456789012:role/Diagnostics"
  }]
}
```

In restricted-role access, the source identity should not need administrator or direct diagnostic
service access. Console EC2 access denied **as this user** is therefore expected.

## Diagnostics role

This section is optional. `--access-mode role` can be used to require it explicitly.
Create `Diagnostics`, or reuse a dedicated role that meets these requirements.
Trust only the intended source principal. For an IAM user, a trust policy is:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {
      "AWS": "arn:aws:iam::123456789012:user/diagnostics-login"
    },
    "Action": "sts:AssumeRole"
  }]
}
```

For an SSO source, the administrator must use the appropriate IAM role ARN and
organization-approved trust conditions; an `arn:aws:sts::...:assumed-role/...`
session ARN is not the IAM role ARN.

Attach a customer-managed policy containing
[`read-only-policy.json`](../examples/read-only-policy.json) to this role. For a
Mumbai-only deployment, use
[`read-only-policy-ap-south-1.json`](../examples/read-only-policy-ap-south-1.json)
instead. Region restrictions belong to this role policy, not merely the client
default region. The Mumbai example permits diagnostic access to all instances in
that region and retains limited global identity/IAM checks.

## Permissions boundaries and organization restrictions

A permissions policy grants access; a permissions boundary caps that access and
does not grant permissions by itself. Check any boundary already attached to the
source identity and diagnostics role. Do not attach the diagnostics-only policy
to the source as a boundary: its explicit deny excludes browser authorization and
`sts:AssumeRole`, which the source needs.
For direct access, use the direct-read example plus the Console login permissions
instead of attaching the role-specific explicit-deny policy to the login identity.

Keep organization-required boundaries in place. Have the administrator review
the source's browser-login and role-assumption actions, role trust, and the
diagnostics role's permitted reads together with any organization SCPs. Do not
solve a denied diagnostic read by granting administrator access. A message saying
`no permissions boundary allows ...` identifies a boundary restriction on the
principal making that operation; inspect that principal, not a different role.

## Configure and verify

```sh
aws-diagnostics-mcp setup --no-login --profile diagnostics --region ap-south-1 --role-arn arn:aws:iam::123456789012:role/Diagnostics --config /actual/path/to/client-mcp.json
```

Use the workload's allowed region and actual client config path. Reload the MCP
client, call `connect_aws`, and approve with the source identity. Then:

1. `connection_status` must return `connected`, an
   `arn:aws:sts::...:assumed-role/Diagnostics/...` principal, and
   `read_only_enforced: true`.
2. `discover_instances` in an allowed region must return the expected inventory.
3. Diagnose a selected instance. Review permission/data gaps separately from
   successful checks. Optional IAM/telemetry permissions may be intentionally
   narrower in a particular deployment.
4. With the Mumbai policy, discovery in a different region must be denied.

If a profile was previously bound to root, use a separate profile for the non-root
identity. The tool does not silently overwrite a profile with a different login
identity. See [session duration and refresh](AUTHENTICATION.md#session-duration).

AWS references: [Console login permissions](https://docs.aws.amazon.com/aws-managed-policy/latest/reference/SignInLocalDevelopmentAccess.html),
[role creation and trust](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_create_for-user.html),
[permissions boundaries](https://docs.aws.amazon.com/IAM/latest/UserGuide/access_policies_boundaries.html).
