#!/usr/bin/env node
/**
 * 9/2 완료 판정용 도구 — PR diff에서 hunk 배열과 앵커가 정확히 나오는지 눈으로 확인한다.
 *
 *   node packages/core/dist/cli/diff-cli.js --repo ../demo-repo --base main --head pr-1-auth-retry
 */
import { collectHunks } from '../diff';

interface Args { repo: string; base: string; head: string; json: boolean }

function parseArgs(argv: string[]): Args {
  const out: Record<string, string> = {};
  let json = false;
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i]!;
    if (a === '--json') { json = true; continue; }
    if (a.startsWith('--')) out[a.slice(2)] = argv[++i] ?? '';
  }
  return { repo: out.repo ?? process.cwd(), base: out.base ?? 'main', head: out.head ?? 'HEAD', json };
}

function main(): void {
  const args = parseArgs(process.argv.slice(2));
  const result = collectHunks({ repoPath: args.repo, baseRef: args.base, headRef: args.head });

  if (args.json) {
    process.stdout.write(JSON.stringify(result, null, 2) + '\n');
    return;
  }

  console.log(`files ${result.files.length}  hunks ${result.hunks.length}  +${result.totalAdditions} -${result.totalDeletions}`);
  for (const f of result.files) {
    console.log(`  ${f.status.padEnd(8)} ${f.file}  +${f.additions} -${f.deletions}  hunks=${f.hunkCount}${f.binary ? '  (binary)' : ''}`);
  }
  console.log('');
  for (const h of result.hunks) {
    console.log(`  ${h.anchor}  +${h.added.length} -${h.removed.length}`);
  }
}

main();
