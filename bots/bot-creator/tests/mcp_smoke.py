import asyncio,json,sys
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
async def main():
    params=StdioServerParameters(command=sys.executable,args=[str(Path(__file__).resolve().parents[1]/'server.py')])
    async with stdio_client(params) as (r,w):
        async with ClientSession(r,w) as client:
            await client.initialize()
            tools=(await client.list_tools()).tools
            assert {'create_bot','patch_bot','update_bot','rollback_bot','get_bot'} <= {t.name for t in tools}
            result=await client.call_tool('get_bot',{'bot_id':'bot-creator'})
            assert not result.isError
            print('PASS real MCP handshake, typed schemas, native get_bot;',len(tools),'tools')
            bad=await client.call_tool('patch_bot',{'bot_id':'bot-creator','revision':'stale','changes':{'tools_remove':['nonexistent']}})
            assert bad.isError
            print('PASS MCP errors cannot report false success')
asyncio.run(main())
