import io
from unittest.mock import Mock

import pytest
from botocore.exceptions import LoginRefreshRequired, LoginTokenLoadError, ProfileNotFound

from aws_diagnostics.auth import Connection, error_result


@pytest.mark.parametrize("error", [LoginRefreshRequired(), LoginTokenLoadError(error_msg="secret"),
                                  ProfileNotFound(profile="new-profile")])
def test_console_expiry_and_new_profile_require_connect(error):
    assert error_result(error)["status"] == "reconnect_required"
    assert "secret" not in str(error_result(error))


@pytest.mark.parametrize("remote", [True, False])
def test_console_command_and_missing_profile(monkeypatch, remote):
    connection = Connection(auth_mode="console")
    monkeypatch.setattr(connection, "identity", lambda: {"status": "reconnect_required"})
    monkeypatch.setattr("aws_diagnostics.auth.shutil.which", lambda _: "aws")
    monkeypatch.setattr("aws_diagnostics.auth.subprocess.run",
                        Mock(return_value=Mock(stdout="aws-cli/2.38.5", stderr="")))
    process = Mock()
    process.poll.return_value = None
    launch = Mock(return_value=process)
    monkeypatch.setattr("aws_diagnostics.auth.subprocess.Popen", launch)
    monkeypatch.setattr("aws_diagnostics.auth.threading.Thread", Mock())
    monkeypatch.setattr("aws_diagnostics.auth.time.sleep", lambda _: None)
    connection.connect(remote=remote)
    command = launch.call_args.args[0]
    assert command[:2] == ["aws", "login"]
    assert ("--remote" in command) == remote
    assert "--region" in command
    assert "--no-browser" not in command
    connection.close()
    connection.process = None


@pytest.mark.parametrize("remote", [True, False])
def test_console_url_needs_no_sso_code(remote):
    connection = Connection(auth_mode="console")
    connection.login_id = "login-id"
    url = "https://us-east-1.signin.aws.amazon.com/v1/authorize?state=abc"
    process = Mock(stdout=io.StringIO(f"Open the following URL:\n{url}\n"))
    def wait():
        assert connection.login["connect_url"] == url
        assert connection.login["status"] == ("authorization_code_required" if remote else "login_required")
        assert connection.login["login_id"] == "login-id"
        return 0
    process.wait.side_effect = wait
    connection._read_login(process, "console", remote)
    assert connection.login["status"] == "login_complete"


def test_code_sent_once_without_echo():
    connection = Connection(auth_mode="console")
    process = Mock()
    process.poll.return_value = None
    connection.process = process
    connection.login = {"status": "authorization_code_required", "login_id": "current"}
    assert connection.complete("old", "sensitivecode")["status"] == "not_found"
    process.stdin.write.assert_not_called()
    result = connection.complete("current", "sensitivecode")
    assert result["status"] == "completing"
    assert "sensitivecode" not in str(result) + str(connection.login)
    process.stdin.write.assert_called_once_with("sensitivecode\n")
    assert connection.complete("current", "sensitivecode")["status"] == "not_found"
    connection.process = None


@pytest.mark.parametrize("code", ["code\ny\n", "", "x" * 16385])
def test_rejects_invalid_code_input(code):
    assert Connection().complete("id", code)["status"] == "invalid_input"


def test_profile_switch_is_not_silently_approved():
    connection = Connection(auth_mode="console")
    process = Mock(stdout=io.StringIO("Do you want to overwrite it to use another identity? (y/n): "))
    process.wait.return_value = 1
    connection._read_login(process, "console", True)
    process.terminate.assert_called_once()
    assert "Different AWS identity" in connection.login["message"]
    process.stdin.write.assert_not_called()


def test_old_cli_fails_before_launch(monkeypatch):
    connection = Connection(auth_mode="console")
    monkeypatch.setattr(connection, "identity", lambda: {"status": "reconnect_required"})
    monkeypatch.setattr("aws_diagnostics.auth.shutil.which", lambda _: "aws")
    monkeypatch.setattr("aws_diagnostics.auth.subprocess.run",
                        Mock(return_value=Mock(stdout="aws-cli/2.20.0", stderr="")))
    launch = Mock()
    monkeypatch.setattr("aws_diagnostics.auth.subprocess.Popen", launch)
    assert connection.connect()["status"] == "setup_required"
    launch.assert_not_called()


def test_auto_mode_preserves_sso_and_defaults_new_profiles_to_console(monkeypatch):
    connection = Connection(auth_mode="auto")
    session = Mock()
    session._session.full_config = {"profiles": {connection.profile: {"sso_session": "company"}}}
    monkeypatch.setattr(connection, "base_session", lambda: session)
    assert connection.mode() == "sso"
    monkeypatch.setattr(connection, "base_session", Mock(side_effect=ProfileNotFound(profile="new")))
    assert connection.mode() == "console"


def test_real_subprocess_remote_code_handoff():
    import subprocess
    import sys
    import threading
    import time

    connection = Connection(auth_mode="console")
    connection.login_id = "subprocess-login"
    # Simulates CLI I/O only: no AWS network, credentials, or browser is involved.
    script = (
        "import sys; "
        "print('https://us-east-1.signin.aws.amazon.com/v1/authorize?state=test', flush=True); "
        "code=input('Enter the authorization code: '); "
        "sys.exit(0 if code == 'testcode' else 1)"
    )
    process = subprocess.Popen([sys.executable, "-u", "-c", script], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    connection.process = process
    reader = threading.Thread(target=connection._read_login, args=(process, "console", True), daemon=True)
    reader.start()
    try:
        deadline = time.monotonic() + 5
        while connection.login["status"] != "authorization_code_required" and time.monotonic() < deadline:
            time.sleep(.02)
        assert connection.login["status"] == "authorization_code_required"
        assert connection.complete("subprocess-login", "testcode")["status"] == "completing"
        reader.join(timeout=5)
        assert not reader.is_alive()
        assert connection.login["status"] == "login_complete"
    finally:
        connection.close()
        process.wait(timeout=5)


def test_native_sdk_console_provider_is_available(monkeypatch, tmp_path):
    import boto3
    config = tmp_path / "config"
    config.write_text("[profile console-test]\nlogin_session = arn:aws:iam::123456789012:root\nregion = us-east-1\n")
    monkeypatch.setenv("AWS_CONFIG_FILE", str(config))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "absent-credentials"))
    monkeypatch.setenv("AWS_LOGIN_CACHE_DIRECTORY", str(tmp_path / "empty-cache"))
    session = boto3.Session(profile_name="console-test")
    # Exercise the real provider and its CRT dependency without using a real token.
    with pytest.raises(LoginTokenLoadError):
        session.get_credentials().get_frozen_credentials()
