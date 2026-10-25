"""One-time local setup using AWS's official, current-user installers."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from urllib.request import urlopen

INSTALL_GUIDE = "https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html"


def find_aws(prefer_user=False):
    explicit = os.getenv("AWS_DIAGNOSTICS_AWS_CLI")
    if explicit and not prefer_user:
        return explicit
    executable = shutil.which("aws")
    if executable and not prefer_user:
        return executable
    if sys.platform == "win32":
        candidates = [Path(os.getenv("LOCALAPPDATA", Path.home() / "AppData/Local"))
                      / "Programs/Amazon/AWSCLIV2/aws.exe",
                      Path(os.getenv("ProgramFiles", "C:/Program Files")) / "Amazon/AWSCLIV2/aws.exe"]
    else:
        candidates = [Path(os.getenv("XDG_BIN_HOME", Path.home() / ".local/bin")) / "aws",
                      Path("/usr/local/bin/aws")]
    return next((str(path) for path in candidates if path.is_file()), executable)


def install_aws():
    """Download over HTTPS, then run the official user installer without a shell."""
    if sys.platform == "win32":
        runner = shutil.which("powershell.exe") or shutil.which("pwsh")
        name = "install.ps1"
        prefix = [runner, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"] if runner else None
    elif sys.platform in {"linux", "darwin"}:
        runner = shutil.which("bash")
        name = "install.sh"
        prefix = [runner] if runner else None
    else:
        raise ValueError(f"Automatic AWS CLI installation is unsupported on {sys.platform}. See {INSTALL_GUIDE}")
    if not prefix:
        raise ValueError(f"Installer shell is unavailable. Install AWS CLI manually: {INSTALL_GUIDE}")
    installer_env = dict(os.environ)
    if sys.platform == "win32":
        # A Python child of PowerShell 7 may inherit its module directories.
        # Let Windows PowerShell rebuild its own path so signature verification
        # loads the matching Microsoft.PowerShell.Security module.
        installer_env = {key: value for key, value in installer_env.items()
                         if key.upper() != "PSMODULEPATH"}
    # AWS publishes these scripts for current-user installation on all three OSes.
    with tempfile.TemporaryDirectory(prefix="aws-diagnostics-setup-") as directory:
        script = Path(directory) / name
        with urlopen(f"https://awscli.amazonaws.com/v2/{name}", timeout=60) as response:
            script.write_bytes(response.read())
        result = subprocess.run([*prefix, str(script)], timeout=600,
                                stdout=sys.stderr, stderr=sys.stderr,
                                env=installer_env,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode:
            raise ValueError(f"AWS CLI installer failed (exit {result.returncode}). See {INSTALL_GUIDE}")
    executable = find_aws(prefer_user=True)
    if not executable:
        raise ValueError("Installer completed but AWS CLI was not found. Restart your terminal and run setup again.")
    return executable


def login(executable, profile, region, auth_mode, remote):
    """Keep login in the foreground so exiting setup cannot cancel browser approval."""
    from .auth import Connection
    connection = Connection(profile=profile, region=region, auth_mode=auth_mode)
    try:
        current = connection.identity()
        if current["status"] == "connected":
            return {"status": "connected"}
        if current["status"] != "reconnect_required":
            return current
        mode = connection.mode()
        command = [executable]
        if mode == "sso":
            command += ["sso", "login", "--use-device-code", "--no-browser"]
        else:
            command += ["login"] + (["--remote"] if remote else [])
        command += ["--profile", profile, "--region", region, "--no-cli-pager"]
        result = subprocess.run(command, timeout=600, stdout=sys.stderr, stderr=sys.stderr,
                                env={**os.environ, "AWS_CLI_AUTO_PROMPT": "off", "AWS_PAGER": ""})
        if result.returncode:
            return {"status": "login_failed", "message": "Retry Connect AWS from your agent."}
        return {"status": connection.identity()["status"]}
    finally:
        connection.close()
