// Normalises raw `n8n export:workflow --separate` output for git.
// Runs with plain Node inside the n8n container (no dependencies).
//
//   SRC_DIR  raw export directory (files named <id>.json)
//   DST_DIR  output directory (files named <workflow_name_slug>.json)
//
// - drops pinData: pinned data can contain real execution payloads
// - drops staticData: runtime state (polling cursors etc.), not definition
// - drops volatile metadata that only creates diff noise
// - skips archived workflows
// - fails if a node carries credential fields other than {id, name}
const fs = require('fs');
const path = require('path');

const src = process.env.SRC_DIR;
const dst = process.env.DST_DIR;
if (!src || !dst) {
  console.error('normalize-workflows: SRC_DIR and DST_DIR are required');
  process.exit(2);
}

const VOLATILE_KEYS = [
  'pinData',
  'staticData',
  'createdAt',
  'updatedAt',
  'shared',
  'versionId',
  'activeVersionId',
  'activeVersion',
  'versionCounter',
  'triggerCount',
  'isArchived',
  'versionMetadata',
  'sourceWorkflowId',
];

const slugify = (name) =>
  name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '');

const seen = new Map();
let written = 0;

for (const file of fs.readdirSync(src).filter((f) => f.endsWith('.json')).sort()) {
  const wf = JSON.parse(fs.readFileSync(path.join(src, file), 'utf8'));

  if (wf.isArchived) {
    console.log(`skip (archived): ${wf.name}`);
    continue;
  }

  for (const node of wf.nodes || []) {
    for (const [type, ref] of Object.entries(node.credentials || {})) {
      const extra = Object.keys(ref || {}).filter((k) => k !== 'id' && k !== 'name');
      if (extra.length) {
        console.error(`normalize-workflows: "${wf.name}" node "${node.name}" credential ${type} has fields ${extra}`);
        process.exit(1);
      }
    }
  }

  for (const key of VOLATILE_KEYS) delete wf[key];
  if (wf.meta) delete wf.meta.instanceId;

  const slug = slugify(wf.name || '');
  if (!slug) {
    console.error(`normalize-workflows: workflow ${wf.id} has no usable name`);
    process.exit(1);
  }
  if (seen.has(slug)) {
    console.error(`normalize-workflows: "${wf.name}" and "${seen.get(slug)}" both map to ${slug}.json; rename one`);
    process.exit(1);
  }
  seen.set(slug, wf.name);

  fs.writeFileSync(path.join(dst, `${slug}.json`), JSON.stringify(wf, null, 2) + '\n');
  console.log(`export: ${wf.name} -> workflows/${slug}.json`);
  written++;
}

console.log(`normalize-workflows: ${written} workflow(s)`);
