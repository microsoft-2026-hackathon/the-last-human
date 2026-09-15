/**
 * diff 파싱 — base 브랜치와의 차이를 hunk 단위로 쪼개고 고정 앵커를 만든다.
 *
 * 앵커가 흔들리면 질문과 답의 대조가 불가능해진다.
 * 앵커 생성 규칙(`file:L<newStart>`)은 확장과 Action이 같아야 한다.
 */
import { execFileSync } from 'node:child_process';
import parseDiff from 'parse-diff';
import type { DiffResult, FileChange, FileStatus, Hunk } from './types';

export interface DiffOptions {
  repoPath: string;
  baseRef: string;
  headRef: string;
  /** git diff --unified 값. 기본 3. */
  context?: number;
}

/** 앵커 문자열을 만드는 단 하나의 지점. */
export function makeAnchor(file: string, newStart: number): string {
  return `${file}:L${newStart}`;
}

/** 앵커를 되돌려 읽는다. 모델이 만든 앵커를 검증할 때 쓴다. */
export function parseAnchor(anchor: string): { file: string; line: number } | null {
  const m = /^(.+):L(\d+)$/.exec(anchor);
  if (!m) return null;
  return { file: m[1]!, line: Number(m[2]) };
}

/**
 * `base...head` 3점 표기를 쓴다. 머지 베이스 기준이라
 * base 브랜치가 앞서 나가도 PR이 실제로 건드린 것만 잡힌다.
 */
export function readRawDiff(opts: DiffOptions): string {
  const context = opts.context ?? 3;
  return execFileSync(
    'git',
    ['diff', `--unified=${context}`, '--no-color', `${opts.baseRef}...${opts.headRef}`],
    { cwd: opts.repoPath, encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 },
  );
}

function statusOf(f: parseDiff.File): FileStatus {
  if (f.new) return 'added';
  if (f.deleted) return 'deleted';
  if (f.from && f.to && f.from !== f.to && f.from !== '/dev/null' && f.to !== '/dev/null') {
    return 'renamed';
  }
  return 'modified';
}

/** `/dev/null`이나 undefined를 걸러 실제 경로를 고른다. */
function pathOf(f: parseDiff.File): string | null {
  const to = f.to && f.to !== '/dev/null' ? f.to : null;
  const from = f.from && f.from !== '/dev/null' ? f.from : null;
  return to ?? from;
}

/** 원문 diff를 hunk 배열로 쪼갠다. git 실행 없이 테스트할 수 있게 분리해 둔다. */
export function parseHunks(raw: string): DiffResult {
  const files: FileChange[] = [];
  const hunks: Hunk[] = [];
  let totalAdditions = 0;
  let totalDeletions = 0;

  for (const f of parseDiff(raw)) {
    const file = pathOf(f);
    if (!file) continue;

    const status = statusOf(f);
    const binary = f.chunks.length === 0 && (f.additions ?? 0) === 0 && (f.deletions ?? 0) === 0;

    files.push({
      file,
      previousFile: status === 'renamed' ? f.from : undefined,
      status,
      binary,
      additions: f.additions ?? 0,
      deletions: f.deletions ?? 0,
      hunkCount: f.chunks.length,
    });
    totalAdditions += f.additions ?? 0;
    totalDeletions += f.deletions ?? 0;

    for (const ch of f.chunks) {
      // parse-diff의 Chunk는 normal/add/del 유형만 온다. combined diff는 다루지 않는다.
      const changes = (ch.changes ?? []) as parseDiff.Change[];
      const added: string[] = [];
      const removed: string[] = [];
      for (const c of changes) {
        if (c.type === 'add') added.push(c.content);
        else if (c.type === 'del') removed.push(c.content);
      }
      // 순수 문맥 hunk는 질문 대상이 아니다.
      if (added.length === 0 && removed.length === 0) continue;

      const newStart = 'newStart' in ch && typeof ch.newStart === 'number' ? ch.newStart : 1;
      const oldStart = 'oldStart' in ch && typeof ch.oldStart === 'number' ? ch.oldStart : 1;

      hunks.push({
        file,
        newStart,
        oldStart,
        anchor: makeAnchor(file, newStart),
        added,
        removed,
        body: changes.map((c) => c.content).join('\n'),
        fileStatus: status,
      });
    }
  }

  return { hunks, files, totalAdditions, totalDeletions };
}

/** git 실행 + 파싱. 확장과 Action의 공통 진입점. */
export function collectHunks(opts: DiffOptions): DiffResult {
  return parseHunks(readRawDiff(opts));
}
