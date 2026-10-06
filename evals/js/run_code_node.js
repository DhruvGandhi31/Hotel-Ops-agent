// Runs the jsCode of one Code node from a workflow export, outside n8n, for tests.
//
//   node evals/js/run_code_node.js < request.json > result.json
//
// request: { workflow: "<path to workflow json>", node: "<node name>", root: "<repo root>",
//            cases: [ { input: {...}, runIndex: 0, nodes: { "<name>": { json, binary, params } },
//                       buffer: "<base64 of the uploaded file>", execution: {id}, workflow: {id, name} } ] }
// result:  [ { output: [...items] } | { error: "message" } ]   one entry per case
//
// It mimics the parts of the n8n Code node these workflows use: $input.first(), $('Node') with
// .first()/.last()/.params, $runIndex, $execution, $workflow, this.helpers.getBinaryDataBuffer, and a
// `require` limited to fs and crypto (as in docker-compose.yml). Paths under /opt/hotel-ops are
// mapped to the repo, where the container mounts prompts/ and schemas/.
const fs = require('fs');
const nodeCrypto = require('crypto');
const path = require('path');

const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;

const request = JSON.parse(fs.readFileSync(0, 'utf8'));
const workflow = JSON.parse(fs.readFileSync(request.workflow, 'utf8'));
const node = workflow.nodes.find((n) => n.name === request.node);
if (!node) throw new Error(`no node named ${request.node}`);

const remap = (p) => (String(p).startsWith('/opt/hotel-ops') ? path.join(request.root, String(p).slice('/opt/hotel-ops'.length)) : p);
const sandboxFs = new Proxy(fs, {
  get: (target, prop) =>
    typeof target[prop] === 'function'
      ? (p, ...rest) => target[prop](typeof p === 'string' ? remap(p) : p, ...rest)
      : target[prop],
});
const sandboxRequire = (name) => {
  if (name === 'fs') return sandboxFs;
  if (name === 'crypto') return nodeCrypto;
  throw new Error(`Module '${name}' is disallowed`);
};

const results = [];
(async () => {
  for (const c of request.cases) {
    const items = (name) => {
      const n = (c.nodes ?? {})[name];
      if (!n) throw new Error(`node '${name}' did not run`);
      return [{ json: n.json ?? {}, binary: n.binary }];
    };
    const $ = (name) => ({
      first: () => items(name)[0],
      last: () => items(name).at(-1),
      get params() {
        return (c.nodes ?? {})[name]?.params ?? {};
      },
    });
    const $input = { first: () => ({ json: c.input ?? {}, binary: c.binary }) };
    const thisArg = {
      helpers: { getBinaryDataBuffer: async () => Buffer.from(c.buffer ?? '', 'base64') },
    };
    try {
      const fn = new AsyncFunction('$input', '$', '$runIndex', '$execution', '$workflow', 'require', node.parameters.jsCode);
      const output = await fn.call(
        thisArg,
        $input,
        $,
        c.runIndex ?? 0,
        c.execution ?? { id: 'test-exec' },
        c.workflow ?? { id: 'test-wf', name: 'Test Workflow' },
        sandboxRequire,
      );
      results.push({ output });
    } catch (e) {
      results.push({ error: String(e.message ?? e) });
    }
  }
  process.stdout.write(JSON.stringify(results));
})();
