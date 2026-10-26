# Distribution and releases

Repository: [AbdullahXDeveloper/hermes-aws-diagnostics-agent](https://github.com/AbdullahXDeveloper/hermes-aws-diagnostics-agent).
The commands below work after the source/tag or release asset has been published.

## Install from GitHub

```sh
pipx install "git+https://github.com/AbdullahXDeveloper/hermes-aws-diagnostics-agent.git@main"
aws-diagnostics-mcp setup --no-login --config /actual/path/to/client-mcp.json
```

Replace the config path with your client's actual path. For reproducible installs,
use `@v1.1.0` instead of `@main` once that tag exists. On VPS add `--remote`.
Reload your agent and ask **Connect AWS**. For company SSO, configure the profile
first. For terminal login during setup, omit `--no-login`. Default auto access uses
an existing profile directly unless a role ARN is configured. To use an optional
restricted role, add `--role-arn` with that role's ARN; see [IAM setup](IAM_SETUP.md).

Python 3.11+, pipx and Git are required for source installation. A downloaded
release wheel needs no Git or PyPI publication:

```sh
pipx install ./aws_diagnostics_mcp-1.1.0-py3-none-any.whl
```

From a cloned or extracted source checkout, use `pipx install .`.

## Configuration and updates

`setup` checks/installs AWS CLI with AWS's official current-user installer.
It stores the resolved executable in `AWS_DIAGNOSTICS_AWS_CLI`, including when
the parent terminal's PATH has not refreshed. Use `--no-install` for managed hosts.
`doctor` checks local prerequisites without login. `configure` generates MCP JSON
without software installation or login.

Only JSON with an `mcpServers` object can be merged automatically. For other
formats, register the command, args and env from setup's printed configuration.
Never pass a TOML/YAML config to the JSON helper. Setup preserves other JSON
entries and backs up changed files. Use `--replace` only to replace a conflicting
`aws-diagnostics` entry. After moving/reinstalling the package, regenerate config.

To update after a new release is published:

```sh
pipx install --force "git+https://github.com/AbdullahXDeveloper/hermes-aws-diagnostics-agent.git@main"
```

Then rerun setup and reload the MCP client. To remove, delete the server entry
from the client config and run `pipx uninstall aws-diagnostics-mcp`.
AWS CLI, its profiles and caches are retained.

## Files to publish

Include `.github/`, `.gitignore`, `src/aws_diagnostics/`, `tests/`, `docs/`,
`examples/`, `pyproject.toml`, `MANIFEST.in`, `README.md`, `CHANGELOG.md` and `LICENSE`.

Exclude `.venv/`, `.tmp/`, caches, `*.egg-info/`, `.aws/`, `.env*`, local MCP
config/backups, diagnosis reports, `build/` and `dist/`. These are dependencies,
generated artifacts or local data. Keep the environment/config locally for testing.
Release wheels and source archives go on GitHub Releases, not into source control.

## First publication

The checkout can be prepared locally without pushing. Review `git status` and
`git diff --cached` before the first commit. Set your own Git author identity if
Git requests it. For a new empty remote repository:

```sh
git add .github .gitignore src/aws_diagnostics tests docs examples pyproject.toml MANIFEST.in README.md CHANGELOG.md LICENSE
git commit -m "Prepare AWS diagnostics MCP 1.1.0"
git push -u origin main
```

If the remote already has commits, fetch and reconcile its history before pushing.
Never force-push to bypass this. Do not upload the entire desktop/project folder
including local dependencies. A clean source ZIP can also be extracted and used
with GitHub's upload UI; include the hidden `.github` and `.gitignore` entries.

## Release validation

1. Follow [development instructions](DEVELOPMENT.md) to run the full tests, build
   wheel/sdist and run `twine check`.
2. Install the wheel into a fresh environment outside the checkout and confirm
   `--version`, `configure` and a real MCP stdio handshake work.
3. Inspect archive members for local config, reports, credentials and caches.
4. Push reviewed source. CI tests Windows/Linux on Python 3.11/3.12 and uploads
   build artifacts. It does not publish releases or contact AWS.
5. Once CI passes, create tag `v1.1.0` and a GitHub release with its wheel/sdist.
   Update the version for later releases; never reuse an existing release version.
6. Verify the published GitHub install URL, then record the Hermes before/after
   demo. Local tests do not establish real-agent integration.
