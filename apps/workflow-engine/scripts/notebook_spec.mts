// Notebook browser cells -> one Playwright test file, using test-builder's own codegen (generateSpec).
// Input (stdin JSON): {title, generated_at, cells: [{name, title, base_url, steps, credential_keys}]}
// Output (stdout): the .spec.ts text. Run by GET /api/notebooks/{id}/export.spec.ts; needs node >= 22.6 (type stripping).
import { generateSpec, type CodegenInput } from '../../test-builder/src/codegen.ts';

interface CellIn {
  name: string;
  title: string;
  base_url: string;
  steps: CodegenInput['steps'];
  credential_keys: string[];
}

const chunks: Buffer[] = [];
for await (const c of process.stdin) chunks.push(c as Buffer);
const input = JSON.parse(Buffer.concat(chunks).toString('utf8')) as { title: string; generated_at: string; cells: CellIn[] };

const FIXTURES = './fixtures.ts';
const imports = new Set<string>();
const tests: string[] = [];
const headers: string[] = [];
for (const cell of input.cells) {
  const text = generateSpec({
    goal: `${cell.name} ${cell.title}`,
    planner: 'notebook (recorded recipe, no AI)',
    targetName: cell.base_url || 'target',
    targetPath: cell.base_url || '',
    steps: cell.steps,
    blocks: [],
    credentialKeys: cell.credential_keys,
    fixturesImport: FIXTURES,
    generatedAt: input.generated_at,
  });
  const lines = text.split('\n');
  const at = lines.findIndex((l) => l.startsWith('import {'));
  const m = /^import \{ (.*) \} from /.exec(lines[at] ?? '');
  for (const name of (m?.[1] ?? 'test').split(',').map((s) => s.trim()).filter(Boolean)) imports.add(name);
  headers.push(...lines.slice(0, at).filter((l) => l.startsWith('// Goal:') || l.startsWith('// Steps:') || l.startsWith('// WARNING')));
  tests.push(lines.slice(at + 1).join('\n').trim());
}

const order = ['test', 'expect', 'totp', 'secret', 'filePath', 'enterOtp', 'expectDownload'];
const names = [...imports].sort((a, b) => order.indexOf(a) - order.indexOf(b));
process.stdout.write([
  `// Exported from the notebook ${JSON.stringify(input.title)} at ${input.generated_at}.`,
  `// One test per browser cell. ${FIXTURES} is apps/test-builder/src/fixtures.ts (secret() reads credentials from env).`,
  ...headers,
  `import { ${names.join(', ')} } from '${FIXTURES}';`,
  '',
  tests.join('\n\n'),
  '',
].join('\n'));
