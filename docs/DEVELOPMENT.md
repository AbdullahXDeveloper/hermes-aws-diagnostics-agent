# Development

```sh
python -m venv .venv
python -m pip install -e ".[test,build]"
```

Activate `.venv` before running the pip command (`.venv\Scripts\Activate.ps1`
on Windows or `source .venv/bin/activate` on Linux/macOS).

## Validation

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Tests use real botocore response validation with mocked AWS calls, and launch a
real subprocess for MCP initialization, tool discovery and tool invocation. They
do not contact an AWS account. Live Console/SSO approval and diagnosis require your
AWS environment and are a separate acceptance check.

On restricted Windows environments, pytest may fail to create its temporary
directory. Use a dedicated, disposable directory inside the workspace:

```powershell
.\.venv\Scripts\python.exe -m pytest -q --basetemp .tmp/pytest
```

Pytest clears the selected `--basetemp` directory, so never point it at files you
need to keep. If the MCP tests hang before initialization, run with
`-o faulthandler_timeout=15` to inspect the stack. A stack in
`socket._fallback_socketpair` during asyncio event-loop creation indicates a local
Windows networking/sandbox restriction; the MCP handshake has not started yet.
Run the suite in a normal local terminal or an approved environment that permits
local socket creation. The coroutine's timeout cannot interrupt event-loop
creation. Do not skip the MCP tests to report the full suite as passing.


## Build a release

```sh
python -m build
python -m twine check dist/*.whl dist/*.tar.gz
```

CI runs tests on Windows/Linux with Python 3.11 and 3.12, then builds and checks
the distribution artifacts. The automated suite mocks AWS API calls and software
installation; live agent integration and browser approval are separate checks.
See [distribution](DISTRIBUTION.md) for release steps.
