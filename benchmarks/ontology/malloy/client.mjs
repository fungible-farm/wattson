// Runs inside the container (network=none, loopback only). usage: node client.mjs GET|POST path [json-body-file-or-'-']
import fs from 'node:fs';
const [method, path, bodyArg] = process.argv.slice(2);
const body = bodyArg ? fs.readFileSync(bodyArg === '-' ? 0 : bodyArg, 'utf8') : undefined;
const r = await fetch('http://127.0.0.1:4000/api/v0' + path, { method, headers: { 'content-type': 'application/json' }, body });
console.log(JSON.stringify({ status: r.status, body: await r.text() }));
