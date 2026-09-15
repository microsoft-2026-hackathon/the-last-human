/**
 * 확장과 Action이 공유하는 타입.
 *
 * 이 파일이 두 벌로 갈라지면 확장에서 통과한 PR이 Action에서 막히는 사고가 난다.
 * core 밖에서 이 모양을 다시 선언하지 말 것.
 */

/** diff 한 덩어리. 질문이 가리키는 최소 단위. */
export interface Hunk {
  /** 변경 후 경로. 삭제된 파일이면 변경 전 경로. */
  file: string;
  /** 변경 후 파일에서 이 hunk가 시작하는 줄 번호. */
  newStart: number;
  /** 변경 전 파일에서 이 hunk가 시작하는 줄 번호. */
  oldStart: number;
  /**
   * 질문과 답을 대조하는 고정 앵커. `src/auth/token.ts:L88` 형태.
   * 앵커가 흔들리면 대조가 불가능해지므로 생성 규칙을 바꾸지 말 것.
   */
  anchor: string;
  /** 추가된 줄. diff 마커(`+`)를 포함한 원문. */
  added: string[];
  /** 삭제된 줄. diff 마커(`-`)를 포함한 원문. */
  removed: string[];
  /** 문맥까지 포함한 hunk 전문. 질문 생성 프롬프트에 그대로 넣는다. */
  body: string;
  /** 이 hunk가 속한 파일의 변경 종류. */
  fileStatus: FileStatus;
}

export type FileStatus = 'added' | 'modified' | 'deleted' | 'renamed';

/** 파일 단위 요약. hunk가 없는 변경(바이너리, 순수 rename)도 여기에는 남는다. */
export interface FileChange {
  file: string;
  previousFile?: string;
  status: FileStatus;
  binary: boolean;
  additions: number;
  deletions: number;
  hunkCount: number;
}

export interface DiffResult {
  hunks: Hunk[];
  files: FileChange[];
  /** 추가/삭제 줄 수 합계. risk.ts의 linesChanged 신호가 쓴다. */
  totalAdditions: number;
  totalDeletions: number;
}
