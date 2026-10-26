"""Installable command: serve MCP, produce configuration, and check prerequisites."""
import argparse
from datetime import datetime, timezone
from importlib.metadata import version
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

from .bootstrap import find_aws, install_aws, login
from .policy import valid_role_arn, root_opt_in


def existing_server_env(config_path):
    if config_path is None or not config_path.exists():
        return {}
    if config_path.is_symlink():
        raise ValueError("Refusing to edit a symlink. Use the actual config file path.")
    data = json.loads(config_path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict) or not isinstance(data.get("mcpServers", {}), dict):
        raise ValueError("Expected a JSON object with an mcpServers object. Client config format may differ.")
    entry = data.get("mcpServers", {}).get("aws-diagnostics")
    return entry["env"] if isinstance(entry, dict) and isinstance(entry.get("env"), dict) else {}


def resolve_access_options(access_mode=None, allow_root=None, config_path=None):
    existing = existing_server_env(config_path)
    mode = access_mode if access_mode is not None else os.getenv(
        "AWS_DIAGNOSTICS_ACCESS_MODE", existing.get("AWS_DIAGNOSTICS_ACCESS_MODE", "auto"))
    if mode not in {"auto", "role", "direct"}:
        raise ValueError("--access-mode must be auto, role, or direct")
    root = allow_root if allow_root is not None else os.getenv(
        "AWS_DIAGNOSTICS_ALLOW_ROOT", existing.get("AWS_DIAGNOSTICS_ALLOW_ROOT"))
    return mode, root_opt_in(root)


def resolve_role_arn(role_arn=None, config_path=None):
    """Use an explicit role, process setting, or the existing client registration."""
    role = role_arn if role_arn is not None else os.getenv("AWS_DIAGNOSTICS_ROLE_ARN")
    if role is None:
        role = existing_server_env(config_path).get("AWS_DIAGNOSTICS_ROLE_ARN")
    if role and not valid_role_arn(role):
        raise ValueError("--role-arn / AWS_DIAGNOSTICS_ROLE_ARN must be a complete IAM role ARN, not an STS session ARN")
    return role or None


def configuration(profile="diagnostics", region="us-east-1", auth_mode="auto", remote=False, role_arn=None,
                  access_mode=None, allow_root=None):
    role_arn = resolve_role_arn(role_arn)
    access_mode, allow_root = resolve_access_options(access_mode, allow_root)
    env = {"AWS_DIAGNOSTICS_PROFILE": profile, "AWS_REGION": region,
           "AWS_DIAGNOSTICS_AUTH_MODE": auth_mode, "AWS_DIAGNOSTICS_ACCESS_MODE": access_mode,
           "AWS_DIAGNOSTICS_ALLOW_ROOT": str(allow_root).lower()}
    if remote:
        env["AWS_DIAGNOSTICS_REMOTE"] = "true"
    if role_arn:
        env["AWS_DIAGNOSTICS_ROLE_ARN"] = role_arn
    # Resolve the package's own environment, not whichever python happens to be on PATH.
    return {"mcpServers": {"aws-diagnostics": {
        "command": str(Path(sys.executable).absolute()),
        "args": ["-m", "aws_diagnostics", "serve"], "env": env}}}


def write_configuration(path, generated, replace=False):
    path = Path(path).expanduser().absolute()
    if path.is_symlink():
        raise ValueError("Refusing to edit a symlink. Use the actual config file path.")
    data = {}
    exists = path.exists()
    if exists:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict) or not isinstance(data.get("mcpServers", {}), dict):
        raise ValueError("Expected a JSON object with an mcpServers object. Client config format may differ.")
    servers = data.setdefault("mcpServers", {})
    entry = generated["mcpServers"]["aws-diagnostics"]
    if servers.get("aws-diagnostics") == entry:
        return {"status": "unchanged", "path": str(path)}
    if "aws-diagnostics" in servers and not replace:
        raise ValueError("aws-diagnostics already exists with different settings. Use --replace to update only this entry.")
    servers["aws-diagnostics"] = entry
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if exists:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup = path.with_name(path.name + ".bak-" + stamp)
        shutil.copy2(path, backup)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".mcp-config-", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, indent=2)
            stream.write("\n")
        if exists:
            shutil.copymode(path, temporary)
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()
    return {"status": "configured", "path": str(path),
            "backup": str(backup) if backup else None,
            "next_step": ("Reload your agent, then ask: Connect AWS." if entry.get("env", {}).get("AWS_DIAGNOSTICS_ACCESS_MODE") != "role" or entry.get("env", {}).get("AWS_DIAGNOSTICS_ROLE_ARN") else
                          "Configure an existing diagnostics role with --role-arn before connecting AWS; then reload the MCP server.")}


def doctor(executable=None):
    executable = executable or find_aws()
    checks = {"python": sys.version.split()[0], "package": version("aws-diagnostics-mcp"),
              "aws_cli": {"status": "missing", "message": "Install AWS CLI v2.32+ on this host."}}
    if executable:
        try:
            result = subprocess.run([executable, "--version"], capture_output=True, text=True,
                                    timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            match = re.search(r"aws-cli/(\d+)\.(\d+)\.(\d+)", result.stdout + result.stderr)
            supported = result.returncode == 0 and match and tuple(map(int, match.groups())) >= (2, 32, 0)
            checks["aws_cli"] = {"status": "ok" if supported else "upgrade_required",
                                 "path": executable, "version": match.group(0) if match else "unknown"}
        except (OSError, subprocess.TimeoutExpired):
            checks["aws_cli"] = {"status": "error", "message": "AWS CLI could not run."}
    checks["message"] = "Local prerequisites only; no AWS login or account access attempted."
    return checks


def main(argv=None):
    parser = argparse.ArgumentParser(description="AWS Diagnostics: installable read-only MCP tool for agents.")
    parser.add_argument("--version", action="version", version=version("aws-diagnostics-mcp"))
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("serve", help="Run stdio MCP server (also the default with no arguments).")
    commands.add_parser("doctor", help="Check local prerequisites without accessing AWS.")
    config = commands.add_parser("configure", help="Print MCP JSON or merge into an explicit JSON config file.")
    setup = commands.add_parser("setup", help="Install missing AWS CLI, configure MCP and log in through AWS.")
    for command in (config, setup):
        command.add_argument("--config", type=Path, help="Agent JSON config path; omitted means print only.")
        command.add_argument("--profile", default="diagnostics")
        command.add_argument("--region", default="us-east-1")
        command.add_argument("--auth-mode", choices=["auto", "console", "sso"], default="auto")
        command.add_argument("--remote", action="store_true", help="Use VPS/cross-device Console login.")
        command.add_argument("--role-arn", help="Existing diagnostics role to assume with a restricted session policy.")
        command.add_argument("--access-mode", choices=["auto", "role", "direct"], default=None,
                             help="auto uses a configured role, otherwise the source profile; role requires an ARN; direct uses the source profile.")
        command.add_argument("--allow-root", action=argparse.BooleanOptionalAction, default=None,
                             help="Explicitly allow a root profile in direct mode. Does not restrict root credentials.")
        command.add_argument("--replace", action="store_true", help="Replace an existing aws-diagnostics entry (backup created).")
    setup.add_argument("--no-login", action="store_true", help="Prepare installation/config only; log in from the agent later.")
    setup.add_argument("--no-install", action="store_true", help="Fail if AWS CLI needs installation or upgrade.")
    args = parser.parse_args(argv)
    if args.command in {None, "serve"}:
        from .server import main as serve
        serve()
        return 0
    if args.command == "doctor":
        result = doctor()
        print(json.dumps(result, indent=2))
        return 0 if result["aws_cli"]["status"] == "ok" else 1
    try:
        if args.config:
            args.config = args.config.expanduser().absolute()
        args.role_arn = resolve_role_arn(args.role_arn, args.config)
        args.access_mode, args.allow_root = resolve_access_options(args.access_mode, args.allow_root, args.config)
        if args.command == "setup":
            return run_setup(args)
        result = configuration(args.profile, args.region, args.auth_mode, args.remote, args.role_arn,
                               args.access_mode, args.allow_root)
        if args.config:
            result = write_configuration(args.config, result, args.replace)
        if args.access_mode == "role" and not args.role_arn:
            print("Role mode requires --role-arn. This output is an incomplete configuration template.", file=sys.stderr)
        print(json.dumps(result, indent=2))
        return 0
    except (ValueError, OSError, subprocess.TimeoutExpired) as error:
        print(f"{args.command.capitalize()} failed: {error}", file=sys.stderr)
        return 2


def run_setup(args):
    # Check config conflicts before downloading or installing anything.
    generated = configuration(args.profile, args.region, args.auth_mode, args.remote, args.role_arn,
                              args.access_mode, args.allow_root)
    replace = args.replace
    if args.config and args.config.is_symlink():
        raise ValueError("Refusing to edit a symlink. Use the actual config file path.")
    if args.config and args.config.exists():
        existing = json.loads(args.config.read_text(encoding="utf-8-sig"))
        if not isinstance(existing, dict) or not isinstance(existing.get("mcpServers", {}), dict):
            raise ValueError("Expected a JSON object with an mcpServers object.")
        entry = existing.get("mcpServers", {}).get("aws-diagnostics")
        if "aws-diagnostics" in existing.get("mcpServers", {}):
            comparable = json.loads(json.dumps(entry))
            if isinstance(comparable, dict) and isinstance(comparable.get("env"), dict):
                comparable["env"].pop("AWS_DIAGNOSTICS_AWS_CLI", None)
            if comparable != generated["mcpServers"]["aws-diagnostics"] and not args.replace:
                raise ValueError("aws-diagnostics already exists. Use --replace to update it (backup created).")
            replace = True
    checks = doctor()
    if checks["aws_cli"]["status"] != "ok":
        if args.no_install:
            raise ValueError("AWS CLI v2.32+ is required. Run setup without --no-install to install it.")
        print("Installing AWS CLI for the current user from awscli.amazonaws.com...", file=sys.stderr)
        executable = install_aws()
        checks = doctor(executable)
    if checks["aws_cli"]["status"] != "ok":
        raise ValueError("AWS CLI installation could not be verified. Check your installation and rerun setup.")
    executable = checks["aws_cli"]["path"]
    generated["mcpServers"]["aws-diagnostics"]["env"]["AWS_DIAGNOSTICS_AWS_CLI"] = executable
    uses_role = args.access_mode == "role" or (args.access_mode == "auto" and bool(args.role_arn))
    missing_role = args.access_mode == "role" and not args.role_arn
    result = {"status": "security_setup_required" if missing_role else "ready", "aws_cli": checks["aws_cli"],
              "access": {"mode": "role" if uses_role else "direct", "read_only_enforced": uses_role,
                         "root_opt_in": args.allow_root}}
    result["configuration"] = (write_configuration(args.config, generated, replace)
                               if args.config else generated)
    result["login"] = {"status": "deferred", "message": "Reload your agent and ask Connect AWS."}
    if missing_role:
        result["login"] = {"status": "not_started", "message": "Configure an existing trusted diagnostics role with --role-arn before browser login."}
        result["security"] = {"status": "security_setup_required", "reason": "missing_role_arn",
                              "message": "Local installation is prepared, but AWS diagnostics cannot run without AWS_DIAGNOSTICS_ROLE_ARN. Rerun setup with --role-arn and the same client configuration path."}
    elif not args.no_login:
        try:
            role_options = {"role_arn": args.role_arn} if args.role_arn else {}
            role_options.update(access_mode=args.access_mode, allow_root=args.allow_root)
            result["login"] = login(executable, args.profile, args.region, args.auth_mode, args.remote, **role_options)
        except (OSError, subprocess.TimeoutExpired):
            result["login"] = {"status": "login_failed", "message": "Retry Connect AWS from your agent."}
        if result["login"]["status"] != "connected":
            result["status"] = (result["login"]["status"] if result["login"]["status"] in
                                {"security_setup_required", "setup_required", "error"} else "login_required")
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "ready" else 1
