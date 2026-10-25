# Diagnostic checks

`diagnose_instance` preserves every option across reconnect/resume:

```json
{
  "instance_id": "i-0123456789abcdef0",
  "region": "us-east-1",
  "app_checks": true,
  "log_group": "/apps/web",
  "log_stream": "i-0123456789abcdef0",
  "website_url": "https://example.com:443",
  "source_ip": "203.0.113.9",
  "connection_path": "public",
  "client_port": 54321
}
```

The address above is documentation-only; use the actual client egress/VPN/bastion IP
as seen by the instance. Website tests originate from the MCP server. They are not
the user's path. For SSH issues also set `ssh=true` and, if needed, `ssh_port`.

| Check | Evidence | Remaining limits |
| --- | --- | --- |
| EC2 | Health, SG, ACL, route and gateway API reads | Host firewall/listeners and end-to-end reachability unknown |
| Metrics | CPU, burst credits on T instances, network bytes/packets, per-volume EBS read/write latency | Thirty-minute window, five-minute buckets; no data is not zero |
| Application (optional) | CWAgent `disk_used_percent`, `mem_used_percent`, `procstat_lookup_pid_count` | Requires preconfigured instance dimension; at most 50 series per metric; data older than ten minutes flagged |
| Logs (optional) | Exact group/stream, at most 100 events from last 30 minutes | Caller asserts instance association; bounded sample may omit relevant events; contents are untrusted and may be sensitive |
| Offline SSM | Instance profile, simulated role actions, endpoint status/private DNS/SG inventory | Live agent/metadata/credentials/endpoint path unknown; alternative SSM credential modes may apply |
| Website (optional) | DNS answers, pinned-address TCP connect, verified TLS certificate/hostname | Public addresses only, at most four; no HTTP request; private/mixed DNS answers are not probed |

Each finding reports `evidence`, `confidence`, `missing_checks` and a recommendation.
Configuration observations and metric thresholds do not establish application root
cause. `partial` means a check is unavailable, unknown, optional and not requested,
or sampled. Consult `unavailable_checks` for its exact status.

IAM deployment is separate from code installation: use a non-root source identity
and configure an existing trusted diagnostics role. The example role policy matches
the runtime session policy. Narrow resources/account scope further for your deployment.
The source needs `sts:AssumeRole`; the diagnostics session explicitly denies it,
`ssm:SendCommand` and every other action outside the diagnostic read list.

AWS references: [session policies](https://docs.aws.amazon.com/IAM/latest/UserGuide/access_policies.html),
[EBS metrics and latency](https://docs.aws.amazon.com/ebs/latest/userguide/using_cloudwatch_ebs.html),
[CloudWatch agent metrics](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/metrics-collected-by-CloudWatch-agent.html),
[SSM agent troubleshooting](https://docs.aws.amazon.com/systems-manager/latest/userguide/troubleshooting-ssm-agent.html).
