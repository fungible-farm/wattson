"""Prove upstream MCP reads the original issue workbook without changing its bytes."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SOURCE: Path = Path('/input') / os.environ.get('EXCEL_WORKBOOK_NAME', 'workbook.xlsm')
EXPECTED_SHA256: str = 'e9c0957510783d81d9c9dd0714c3810e81383ff98a1b9f159222cee8bd5db880'
EXPECTED_TOOLS: set[str] = {'describe_workbook', 'describe_sheet', 'read_range'}


def payload(result: object) -> dict:
    """Read the upstream structured tool result; retain errors verbatim."""
    encoded = result.model_dump(by_alias=True)
    if encoded.get('isError'):
        raise RuntimeError(json.dumps(encoded))
    if encoded.get('structuredContent') is not None:
        return encoded['structuredContent']
    return json.loads('\n'.join(item['text'] for item in encoded['content'] if item['type'] == 'text'))


async def main() -> None:
    """Read known original bytes through actual MCP; fail on schema/value/provenance drift."""
    before = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    is_issue4 = before == EXPECTED_SHA256
    escaped = ''.join(chr(92) + format(ord(char), '03o') if char in (' ', chr(9), chr(10), chr(92)) else char for char in str(SOURCE))
    assert any(row.split()[4] == escaped and 'ro' in row.split()[5].split(',')
               for row in Path('/proc/self/mountinfo').read_text().splitlines())
    environment = dict(os.environ)
    # Fresh sideband path prevents an earlier server's manifest from being reused.
    manifest_directory = TemporaryDirectory(prefix='excel-proof-')
    manifest_path = Path(manifest_directory.name) / 'capture.json'
    environment['EXCEL_MCP_CAPTURE_MANIFEST'] = str(manifest_path)
    parameters = StdioServerParameters(command=sys.executable, args=['/opt/excel/server.py'], env=environment)
    async with stdio_client(parameters) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            initialized = await session.initialize()
            captured = json.loads(manifest_path.read_text())
            assert captured['snapshot_sha256'] == before and captured['snapshot_bytes'] == SOURCE.stat().st_size, captured
            snapshot = Path(captured['snapshot_path'])
            assert snapshot.parent != SOURCE.parent and snapshot.name == SOURCE.name
            assert snapshot.stat().st_mode & 0o777 == 0o400
            assert snapshot.parent.stat().st_mode & 0o777 == 0o700
            assert hashlib.sha256(snapshot.read_bytes()).hexdigest() == before
            tools = {tool.name for tool in (await session.list_tools()).tools}
            assert tools == EXPECTED_TOOLS, tools
            workbook = payload(await session.call_tool('describe_workbook', {'path':SOURCE.name}))
            names = [sheet['name'] for sheet in workbook['sheets']]
            assert names, 'workbook has no worksheets'
            if is_issue4:
                assert len(names) == 85 and names[0] == 'Approval sheet', names
            read = payload(await session.call_tool('read_range', {
                'path':SOURCE.name,'sheet':names[0],'range':'A1:C3','mode':'formulas','max_cells':9}))
            if is_issue4:
                assert read['values'][0] == ['Tab','Inputs','Responsible team'], read
                assert read['values'][1][0] == 'ONSG', read
            refusal = (await session.call_tool('describe_workbook', {'path':'/etc/passwd'})).model_dump(by_alias=True)
            assert refusal.get('isError'), refusal
            source_refusal = (await session.call_tool('describe_workbook', {'path':str(SOURCE)})).model_dump(by_alias=True)
            assert source_refusal.get('isError'), source_refusal
            # Diagnose a metadata path separately; streaming read success must not hide its failure.
            details = (await session.call_tool('describe_sheet', {'path':SOURCE.name,'sheet':names[0]})).model_dump(by_alias=True)
    manifest_directory.cleanup()
    metadata_error = bool(details.get('isError'))
    if is_issue4:
        assert metadata_error and '3,000,000' in json.dumps(details) and 'read_range' in json.dumps(details), details
    after = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    assert before == after, 'original input changed'
    report = {'proof':'PASS_STREAMING_READ_WITH_METADATA_LIMIT' if metadata_error else 'PASS_STREAMING_READ','source_sha256':before,'original_unchanged':True,'captured_workbook':captured,'session_bytes_frozen':True,'input_mount_read_only':True,
              'server':initialized.model_dump(by_alias=True)['serverInfo'],'tools':sorted(tools),'sheet_count':len(names),
              'sheet_names_sample':names[:3],'read':read,'outside_path_refused':True,
              'verified_operations':['initialize','list_tools','describe_workbook','read_range','path_refusal'],
              'issue4_known_metadata_limit_reproduced':is_issue4 and metadata_error,
              'describe_sheet_error':metadata_error,
              'describe_sheet_diagnostic':details if details.get('isError') else {'status':'PASS'}}
    print(json.dumps(report,indent=2))


if __name__ == '__main__':
    asyncio.run(main())
