// Exercise the official HF server through its MCP SDK, never through a mock.
import {Client} from '@modelcontextprotocol/client';
import {StdioClientTransport} from '@modelcontextprotocol/client/stdio';
import {writeFileSync} from 'node:fs';

const transport = new StdioClientTransport({
  command: '/opt/hf-mcp/node_modules/.bin/hf-mcp-server',
  env: {...process.env, DISABLE_TOOLS: 'gr1_z_image_turbo_generate'},
});
const client = new Client({name: 'wattson-hf-proof', version: '0.1.0'});
try {
  await client.connect(transport);
  const listed = await client.listTools();
  writeFileSync('/evidence/hf-mcp-tools.json', JSON.stringify(listed, null, 2));
  const tool = listed.tools.find(tool => tool.name === 'hf_fs');
  if (!tool) throw new Error('Official Hub filesystem tool was not advertised');
  const result = await client.callTool({name: 'hf_fs', arguments: {operations: [
    {cmd: 'cat', args: ['hf://models/PromptExecutionAU/wattson-qwen3.5-4b-pyt/README.md']},
    {cmd: 'cat', args: ['hf://models/PromptExecutionAU/wattson-granite-4.2-8b-py/README.md']},
  ]}});
  writeFileSync('/evidence/hf-mcp-model-cards.json', JSON.stringify(result, null, 2));
  if (result.isError) throw new Error('Hub MCP returned a tool error');
  const text = JSON.stringify(result);
  for (const name of ['wattson-qwen3.5-4b-pyt', 'wattson-granite-4.2-8b-py']) {
    if (!text.includes(name)) throw new Error(`Missing model card: ${name}`);
  }
  console.log(JSON.stringify({tools: listed.tools.map(tool => tool.name), modelCardsRead: 2}));
} finally {
  await client.close();
}
