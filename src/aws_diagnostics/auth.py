"""Keep credentials in AWS's cache, never in tool responses."""
import atexit
import json
import os
import re
import shutil
import subprocess
import threading
import time
from uuid import uuid4
from urllib.parse import urlsplit, urlunsplit, urlencode, parse_qsl

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from .bootstrap import find_aws
from .policy import SESSION_POLICY, valid_role_arn

CLIENT_CONFIG = Config(connect_timeout=5, read_timeout=15, retries={"max_attempts": 2})
AUTH_ERRORS = {"ExpiredToken", "ExpiredTokenException", "InvalidClientTokenId",
               "UnrecognizedClientException", "UnauthorizedException"}


def error_result(error):
    """Return safe structured failures, without token-bearing exception text."""
    code = (error.response["Error"]["Code"] if isinstance(error, ClientError)
            else type(error).__name__)
    if code == "DiagnosticsSecurityBoundary":
        return {"status": "security_setup_required", "message":
                "Use a non-root AWS profile and set AWS_DIAGNOSTICS_ROLE_ARN to a trusted diagnostics role. "
                "Diagnostics require an STS session restricted to diagnostic reads; no fallback is allowed."}
    if code in {"DiagnosticsRoleMissing", "DiagnosticsRoleInvalid", "DiagnosticsRootIdentity"}:
        reason, message = {
            "DiagnosticsRoleMissing": ("missing_role_arn",
                "AWS_DIAGNOSTICS_ROLE_ARN is missing. Set the existing diagnostics role ARN in this MCP installation using setup/configure --role-arn, then reload the MCP server. Browser login alone cannot configure the role."),
            "DiagnosticsRoleInvalid": ("invalid_role_arn",
                "AWS_DIAGNOSTICS_ROLE_ARN must be a complete IAM role ARN, not an STS assumed-role session ARN. Correct this MCP installation's configuration and reload the server."),
            "DiagnosticsRootIdentity": ("root_identity",
                "This source profile is logged in as root. Use a separate non-root IAM/SSO profile that can assume the configured diagnostics role."),
        }[code]
        return {"status": "security_setup_required", "reason": reason, "message": message}
    if code in AUTH_ERRORS or code in {
        "UnauthorizedSSOTokenError", "SSOTokenLoadError", "TokenRetrievalError",
        "NoCredentialsError", "PartialCredentialsError", "ProfileNotFound",
        "LoginRefreshRequired", "LoginTokenLoadError",
    }:
        return {"status": "reconnect_required", "message": "Connect AWS to continue."}
    if code in {"InvalidConfigError", "ConfigParseError", "MissingDependencyException"}:
        return {"status": "setup_required", "code": code,
                "message": "Check AWS profile configuration and install project dependencies."}
    if code in {"AccessDenied", "AccessDeniedException", "UnauthorizedOperation"}:
        message = error.response.get("Error", {}).get("Message", "").lower()
        result = {"status": "error", "code": code, "operation": error.operation_name,
                  "message": "AWS denied this operation. Check the diagnostics role policy, source AssumeRole permission and role trust; another login does not fix missing permissions."}
        if "permissions boundary" in message:
            result["policy_type"] = "permissions_boundary"
            result["message"] = "AWS reports a permissions boundary restriction. An administrator must check the boundary on the principal making this operation, along with its permissions policy."
        elif "service control policy" in message:
            result["policy_type"] = "service_control_policy"
            result["message"] = "AWS reports an organization service control policy restriction. Ask the organization administrator to review this operation."
        return result
    if code == "ValidationError" and error.operation_name == "AssumeRole":
        return {"status": "setup_required", "code": code, "operation": "AssumeRole",
                "message": "Check the role ARN, trust and requested role session duration. Duration must not exceed the role maximum; role chaining is limited to 3600 seconds."}
    return {"status": "error", "code": code,
            "message": "AWS check failed. Check permissions, region, or connectivity."}


class Connection:
    def __init__(self, profile=None, region=None, auth_mode=None, role_arn=None):
        self.profile = profile or os.getenv("AWS_DIAGNOSTICS_PROFILE", "diagnostics")
        self.region = region or os.getenv("AWS_REGION", "us-east-1")
        self.auth_mode = auth_mode or os.getenv("AWS_DIAGNOSTICS_AUTH_MODE", "auto")
        self.role_arn = role_arn or os.getenv("AWS_DIAGNOSTICS_ROLE_ARN", "")
        duration = os.getenv("AWS_DIAGNOSTICS_SESSION_DURATION_SECONDS", "3600")
        try:
            self.session_duration = int(duration)
        except ValueError:
            raise ValueError("AWS_DIAGNOSTICS_SESSION_DURATION_SECONDS must be an integer from 900 to 43200") from None
        if not 900 <= self.session_duration <= 43200:
            raise ValueError("AWS_DIAGNOSTICS_SESSION_DURATION_SECONDS must be an integer from 900 to 43200")
        if self.auth_mode not in {"auto", "console", "sso"}:
            raise ValueError("AWS_DIAGNOSTICS_AUTH_MODE must be auto, console, or sso")
        self.lock = threading.RLock()
        self.process = None
        self.login = {"status": "idle"}
        atexit.register(self.close)

    def session(self, region=None):
        # Report configuration errors before starting or examining a login session.
        if not self.role_arn:
            raise ClientError({"Error": {"Code": "DiagnosticsRoleMissing"}}, "AssumeRole")
        if not valid_role_arn(self.role_arn):
            raise ClientError({"Error": {"Code": "DiagnosticsRoleInvalid"}}, "AssumeRole")
        # Fresh session sees CLI login; the SDK refreshes SSO or console credentials.
        base = self.base_session(region)
        sts = base.client("sts", config=CLIENT_CONFIG)
        identity = sts.get_caller_identity()
        if identity["Arn"].endswith(":root"):
            raise ClientError({"Error": {"Code": "DiagnosticsRootIdentity"}}, "AssumeRole")
        credentials = sts.assume_role(RoleArn=self.role_arn, RoleSessionName="aws-diagnostics",
            DurationSeconds=self.session_duration, Policy=json.dumps(SESSION_POLICY, separators=(",", ":")))["Credentials"]
        return boto3.Session(aws_access_key_id=credentials["AccessKeyId"],
            aws_secret_access_key=credentials["SecretAccessKey"],
            aws_session_token=credentials["SessionToken"], region_name=region or self.region)

    def base_session(self, region=None):
        return boto3.Session(profile_name=self.profile, region_name=region or self.region)

    def mode(self):
        if self.auth_mode != "auto":
            return self.auth_mode
        try:
            profiles = self.base_session()._session.full_config.get("profiles", {})
            config = profiles.get(self.profile, {})
            return "sso" if "sso_session" in config or "sso_start_url" in config else "console"
        except BotoCoreError:
            return "console"

    def identity(self):
        try:
            result = self.session().client("sts", config=CLIENT_CONFIG).get_caller_identity()
            return {"status": "connected", "account_id": result["Account"],
                    "principal": result["Arn"], "region": self.region,
                    "read_only_enforced": True, "boundary": "STS explicit-deny session policy"}
        except (BotoCoreError, ClientError) as error:
            return error_result(error)

    def connect(self, remote=None):
        current = self.identity()
        if current["status"] != "reconnect_required":
            return current
        with self.lock:
            if self.process and self.process.poll() is None:
                return dict(self.login)
            executable = find_aws()
            if not executable:
                return {"status": "setup_required", "message": "Install AWS CLI v2.32+ on the MCP server host."}
            mode = self.mode()
            if remote is None:
                setting = os.getenv("AWS_DIAGNOSTICS_REMOTE")
                remote = setting.lower() in {"1", "true", "yes"} if setting else bool(
                    os.getenv("SSH_CONNECTION") or (os.name != "nt" and not os.getenv("DISPLAY")
                    and not os.getenv("WAYLAND_DISPLAY") and os.sys.platform != "darwin"))
            try:
                version = subprocess.run([executable, "--version"], capture_output=True,
                    text=True, timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                match = re.search(r"aws-cli/(\d+)\.(\d+)\.(\d+)", version.stdout + version.stderr)
                minimum = (2, 32, 0) if mode == "console" else (2, 22, 0)
                if not match or tuple(map(int, match.groups())) < minimum:
                    return {"status": "setup_required", "message": "Update AWS CLI to v2.32 or later."}
            except (OSError, subprocess.TimeoutExpired):
                return {"status": "setup_required", "message": "AWS CLI version check failed."}
            command = ([executable, "sso", "login", "--use-device-code", "--no-browser"]
                       if mode == "sso" else [executable, "login"] + (["--remote"] if remote else []))
            command += ["--profile", self.profile, "--region", self.region, "--no-cli-pager"]
            self.login = {"status": "starting", "message": "Preparing AWS sign-in. Call connection_status shortly."}
            try:
                self.process = subprocess.Popen(
                    command,
                    stdin=subprocess.PIPE if mode == "console" and remote else subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace", bufsize=1,
                    env={**os.environ, "AWS_CLI_AUTO_PROMPT": "off", "AWS_PAGER": ""},
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except OSError:
                return {"status": "setup_required", "message": "AWS CLI could not start."}
            process = self.process
            self.login_id = str(uuid4())
            threading.Thread(target=self._read_login, args=(process, mode, remote), daemon=True).start()
            threading.Thread(target=self._timeout, args=(process,), daemon=True).start()
        # Return promptly to the agent; no tool waits for browser approval.
        for _ in range(20):
            time.sleep(0.1)
            with self.lock:
                if self.login["status"] != "starting":
                    break
        with self.lock:
            return dict(self.login)

    def _read_login(self, process, mode="sso", remote=False):
        url = code = None
        failure = None
        try:
            # Prompts need not end with a newline. Bound buffering and never log CLI output.
            line = ""
            while True:
                char = process.stdout.read(1)
                if not char:
                    break
                line = (line + char)[-32768:]
                if "Do you want to overwrite" in line:
                    failure = "Different AWS identity selected. Retry with the original identity or use a separate profile."
                    process.terminate()
                    break
                if char != "\n":
                    continue
                new_url = False
                match = re.search(r"https://[^\s<>]+", line)
                if match:
                    candidate = match.group(0)
                    host = urlsplit(candidate).hostname or ""
                    if host.endswith((".amazonaws.com", ".amazonaws.com.cn", ".awsapps.com",
                                      ".signin.aws.amazon.com")) and (
                        mode == "sso" or "signin" in host.split(".")):
                        url = candidate
                        new_url = True
                match = re.fullmatch(r"\s*([A-Z0-9]{4}-[A-Z0-9]{4})\s*", line)
                if match:
                    code = match.group(1)
                if url and mode == "console" and new_url:
                    with self.lock:
                        self.login = {"status": "authorization_code_required" if remote else "login_required",
                                      "connect_url": url, "login_id": self.login_id,
                                      "message": "Open Connect AWS. " + (
                                          "Return the complete Base64 confirmation value copied from AWS via complete_aws_login, unchanged and without decoding."
                                          if remote else "Approve in this computer's browser, then call connection_status.")}
                elif url and code:
                    parts = urlsplit(url)
                    query = dict(parse_qsl(parts.query))
                    query["user_code"] = code
                    link = urlunsplit(parts._replace(query=urlencode(query)))
                    with self.lock:
                        self.login = {"status": "login_required", "connect_url": link,
                                      "verification_code": code,
                                      "message": "Open Connect AWS and approve the matching code. Then call connection_status."}
                line = ""
            exit_code = process.wait()
            with self.lock:
                self.login = {"status": "login_complete" if exit_code == 0 else "login_failed",
                              "message": failure or ("Check connection_status." if exit_code == 0 else
                                  "Login failed or expired. Check CLI/profile configuration and call connect_aws to retry.")}
        except (OSError, ValueError):
            with self.lock:
                self.login = {"status": "login_failed", "message": "Login process ended. Call connect_aws to retry."}
        finally:
            process.stdout.close()
            if process.stdin:
                process.stdin.close()

    def complete(self, login_id, authorization_code):
        # CLI validates the code's OAuth state and exchanges it. Never echo or persist it here.
        if not re.fullmatch(r"[A-Za-z0-9+/=_-]{1,16384}", authorization_code):
            return {"status": "invalid_input", "message": "Supply the complete Base64 confirmation value copied from AWS as one unbroken string. Do not decode it, extract its inner code, or include preview wrappers."}
        with self.lock:
            if (not self.process or self.process.poll() is not None
                    or self.login.get("status") != "authorization_code_required"
                    or self.login.get("login_id") != login_id):
                return {"status": "not_found", "message": "No matching remote login is waiting. Call connect_aws."}
            try:
                self.process.stdin.write(authorization_code + "\n")
                self.process.stdin.flush()
                self.process.stdin.close()
            except (OSError, ValueError):
                return {"status": "login_failed", "message": "Login process ended. Call connect_aws to retry."}
            self.login = {"status": "completing", "message": "Code submitted. Call connection_status shortly."}
            return dict(self.login)

    def _timeout(self, process):
        try:
            process.wait(timeout=600)
        except subprocess.TimeoutExpired:
            process.terminate()

    def status(self):
        identity = self.identity()
        if identity["status"] == "connected":
            return identity
        with self.lock:
            if self.process and self.process.poll() is None:
                return dict(self.login)
            if self.login["status"] == "login_failed":
                return dict(self.login)
        return identity

    def close(self):
        with self.lock:
            if self.process and self.process.poll() is None:
                self.process.terminate()
