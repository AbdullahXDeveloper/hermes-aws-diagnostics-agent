import asyncio
import sys
import os
import pytest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@pytest.mark.parametrize("arguments", [["-m", "aws_diagnostics.server"], ["-m", "aws_diagnostics", "serve"]])
def test_real_stdio_handshake_and_tool_call(arguments):
    async def run():
        parameters = StdioServerParameters(command=sys.executable,
            args=arguments, env={"AWS_DIAGNOSTICS_PROFILE": "nonexistent-test-profile",
                                 "AWS_DIAGNOSTICS_ROLE_ARN": "arn:aws:iam::123456789012:role/Diagnostics",
                                 "PYTHONPATH": os.pathsep.join(sys.path)})
        async with stdio_client(parameters) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                tools = await session.list_tools()
                assert {t.name for t in tools.tools} == {"connect_aws", "connection_status",
                    "list_regions", "discover_instances", "diagnose_instance", "resume_diagnosis", "complete_aws_login"}
                response = await session.call_tool("diagnose_instance", {"instance_id": "invalid"})
                assert not response.isError
                assert "invalid_input" in response.content[0].text
                response = await session.call_tool("connection_status", {})
                assert not response.isError
                assert "reconnect_required" in response.content[0].text
    asyncio.run(asyncio.wait_for(run(), timeout=30))
