# Project file guide

This project is a reusable Python MCP server. An existing AI agent (for example,
Hermes) launches it, calls its tools, receives AWS evidence, and explains the
results. The server provides read-only diagnostics; it does not itself contain
an AI model or implement automatic infrastructure fixes.

Typical flow: agent -> MCP server -> non-root source login -> restricted STS
role session -> EC2/CloudWatch/SSM/IAM reads -> findings and evidence -> agent.

## Application code: `src/aws_diagnostics/`

| File | Purpose |
| --- | --- |
| [`__init__.py`](../src/aws_diagnostics/__init__.py) | Marks the Python package and describes its scope. |
| [`__main__.py`](../src/aws_diagnostics/__main__.py) | Runs the CLI when called with `python -m aws_diagnostics`. |
| [`cli.py`](../src/aws_diagnostics/cli.py) | Implements `serve`, `setup`, `configure`, `doctor`, and `--version`; generates/merges client configuration with backups. |
| [`bootstrap.py`](../src/aws_diagnostics/bootstrap.py) | Finds/checks AWS CLI, installs it from AWS's official installer when needed, and supports terminal login during setup. |
| [`server.py`](../src/aws_diagnostics/server.py) | Registers the seven MCP tools, gives the client usage instructions, and retains pending diagnoses across reconnects within the same server process. |
| [`auth.py`](../src/aws_diagnostics/auth.py) | Handles Console/SSO login, connection status, root rejection, role assumption with the restrictive session policy, session-duration configuration, and safe error reporting. |
| [`policy.py`](../src/aws_diagnostics/policy.py) | Defines the allowed diagnostic AWS actions and explicit-deny STS session policy. |
| [`diagnostics.py`](../src/aws_diagnostics/diagnostics.py) | Coordinates instance/region discovery and diagnosis, collecting evidence, findings, permission/data gaps, and recommendations. |
| [`telemetry.py`](../src/aws_diagnostics/telemetry.py) | Reads CloudWatch CPU-credit/network/EBS metrics, optional existing CWAgent telemetry, and bounded selected log samples. |
| [`ssm.py`](../src/aws_diagnostics/ssm.py) | Investigates offline SSM using instance profiles, role-policy simulation, and VPC endpoint configuration; reports remaining unknowns. |
| [`ssh.py`](../src/aws_diagnostics/ssh.py) | Evaluates IPv4 security-group, route, and ACL configuration for a supplied connection path. Does not SSH into the instance. |
| [`network.py`](../src/aws_diagnostics/network.py) | Validates optional website targets, probes public DNS/TCP/TLS, and reads internet-gateway attachment evidence. Sends no HTTP request. |

## Tests: `tests/`

| File | Purpose |
| --- | --- |
| [`test_cli.py`](../tests/test_cli.py) | Checks CLI configuration generation, backups, conflict handling, and local prerequisite checks. |
| [`test_setup.py`](../tests/test_setup.py) | Checks setup/install/login orchestration and preservation of existing configuration, with installation and login mocked. |
| [`test_console_login.py`](../tests/test_console_login.py) | Checks Console login commands, remote authorization-code handoff, expired tokens, SDK provider support, and identity-switch handling. |
| [`test_mcp.py`](../tests/test_mcp.py) | Launches the actual server subprocess and verifies MCP initialization, tool discovery, and tool calls. |
| [`test_diagnostics.py`](../tests/test_diagnostics.py) | Checks discovery pagination, findings, missing permissions, IAM error guidance, and reconnect/resume behavior with mocked AWS calls. |
| [`test_ssh.py`](../tests/test_ssh.py) | Checks source CIDRs, security-group aggregation, ACL ordering/ports, route selection, and unknown/incomplete evidence handling. |
| [`test_extended_diagnostics.py`](../tests/test_extended_diagnostics.py) | Checks restricted role sessions, duration settings, no credential fallback, additional telemetry/SSM/website checks, and new-option resume behavior. |

## Configuration examples: `examples/`

These are templates. They are not automatically executed or deployed to AWS.
Replace sample accounts, roles, profiles, paths and regions with each user's values.

| File | Purpose |
| --- | --- |
| [`read-only-policy.json`](../examples/read-only-policy.json) | Example permissions policy for the diagnostics role, matching the runtime read-only session boundary. |
| [`read-only-policy-ap-south-1.json`](../examples/read-only-policy-ap-south-1.json) | Optional Mumbai-only version of the role policy, with limited global identity/IAM exceptions. |
| [`mcp-config.json`](../examples/mcp-config.json) | Example desktop MCP client registration. |
| [`mcp-config-vps.json`](../examples/mcp-config-vps.json) | Example registration for remote/VPS browser authorization. |
| [`aws-config.example`](../examples/aws-config.example) | Example IAM Identity Center/SSO configuration for AWS CLI profiles. |

## Documentation: `docs/`

| File | Purpose |
| --- | --- |
| [`AGENT_INSTALL.md`](AGENT_INSTALL.md) | Installation and verification workflow for an agent setting up this MCP server. |
| [`AUTHENTICATION.md`](AUTHENTICATION.md) | Console/SSO authentication, desktop/VPS differences, session lifetime, daily use, and security limitations. |
| [`IAM_SETUP.md`](IAM_SETUP.md) | One-time source-identity permissions, diagnostics-role trust/policy setup, boundary troubleshooting, and AWS verification. |
| [`DIAGNOSTIC_CHECKS.md`](DIAGNOSTIC_CHECKS.md) | Diagnostic options, evidence sources, known limits, and example requests. |
| [`DEVELOPMENT.md`](DEVELOPMENT.md) | Development environment setup, automated tests, Windows test troubleshooting, and build commands. |
| [`DISTRIBUTION.md`](DISTRIBUTION.md) | Installation/update/removal instructions, publication/release steps, and release validation. |
| [`PROJECT_GUIDE.md`](PROJECT_GUIDE.md) | This map of project files and generated local folders. |

## Project and CI files

| File | Purpose |
| --- | --- |
| [`README.md`](../README.md) | Main overview, installation, usage, permissions model, and documentation entry point. |
| [`pyproject.toml`](../pyproject.toml) | Package metadata/version, Python/dependency requirements, build backend, CLI entry point, and pytest settings. |
| [`MANIFEST.in`](../MANIFEST.in) | Specifies documentation, examples, tests, and license files included in source distributions. |
| [`LICENSE`](../LICENSE) | MIT terms for use, modification, and redistribution. |
| [`CHANGELOG.md`](../CHANGELOG.md) | Version-by-version changes. |
| [`.gitignore`](../.gitignore) | Excludes local configuration, credentials, dependencies, caches, reports, and build output from Git. |
| [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) | Runs Windows/Linux tests for Python 3.11/3.12, builds packages, validates metadata, and smoke-tests the installed wheel. Does not publish releases or contact AWS. |

## Local/generated files and directories

These support one installation or local verification. Their contents are not the
reusable source and should not be committed as a whole.

| Item | Purpose |
| --- | --- |
| `mcp-config.local.json` | This checkout's local MCP registration: executable path and profile/region/role settings. Ignored by Git. |
| `mcp-config.local.json.bak-*` | Copies of earlier local configurations made before editing. |
| `.venv/` | Local Python runtime environment and installed dependencies. |
| `.tmp/` | Disposable test/build environments and verification output. |
| `.tmp/review_verify.py` | Local archive audit and installed-wheel CLI/MCP smoke-check script used during review. |
| `.tmp/review-dist-1.0.0/` | Fresh 1.0.0 wheel and source distribution generated during review. |
| `.tmp/review-wheel-env/` | Separate environment used to install and verify that wheel. |
| `dist/` | Earlier generated distribution artifacts; filenames may refer to older versions. Use a newly validated build for release. |
| `.pytest_cache/` | Pytest's cached run metadata. |
| `src/*.egg-info/` | Generated package metadata used by editable installs/builds. |
| `__pycache__/` | Generated Python bytecode caches. |
| `.git/` | Repository history, branches, index, and local Git metadata. |

The automated suite uses mocked AWS calls. Passing tests and package checks do not
establish a successful live AWS diagnosis or a working integration with every agent.
