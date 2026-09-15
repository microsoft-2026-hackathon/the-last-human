"""확장과 Action이 공유하는 데이터 계약.

이 모양이 두 벌로 갈라지면 한쪽에서 통과한 PR이 다른 쪽에서 막히는 사고가 난다.
lasthuman 밖에서 이 구조를 다시 선언하지 말 것.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

FileStatus = Literal["added", "modified", "deleted", "renamed"]
QuestionType = Literal["claim", "consequence", "rationale"]
Verdict = Literal["pass", "hold"]
Band = Literal["human-led", "co-authored", "agent-led"]


@dataclass(frozen=True)
class Hunk:
    """diff 한 덩어리. 질문이 가리키는 최소 단위."""

    file: str
    new_start: int
    old_start: int
    #: 질문과 답을 대조하는 고정 앵커. ``sample-app/app/auth/token.py:L88`` 형태.
    #: 생성 규칙을 바꾸면 기존 인증의 대조가 전부 깨진다.
    anchor: str
    #: 추가된 줄. diff 마커(``+``)를 포함한 원문.
    added: tuple[str, ...]
    #: 삭제된 줄. diff 마커(``-``)를 포함한 원문.
    removed: tuple[str, ...]
    #: 문맥까지 포함한 hunk 전문. 질문 생성 프롬프트에 그대로 넣는다.
    body: str
    file_status: FileStatus


@dataclass(frozen=True)
class FileChange:
    """파일 단위 요약. hunk가 없는 변경(바이너리, 순수 rename)도 여기에는 남는다."""

    file: str
    status: FileStatus
    binary: bool
    additions: int
    deletions: int
    hunk_count: int
    previous_file: str | None = None


@dataclass(frozen=True)
class DiffResult:
    hunks: tuple[Hunk, ...]
    files: tuple[FileChange, ...]

    @property
    def total_additions(self) -> int:
        return sum(f.additions for f in self.files)

    @property
    def total_deletions(self) -> int:
        return sum(f.deletions for f in self.files)


@dataclass(frozen=True)
class PrMeta:
    """위험 점수가 참고하는 PR 사실. 모델을 부르지 않고 채울 수 있는 것만 담는다."""

    title: str = ""
    body: str = ""
    author: str = ""
    #: OWNER / MEMBER / COLLABORATOR / CONTRIBUTOR / NONE
    author_association: str = "OWNER"
    #: 커밋 트레일러 등에서 관측된 에이전트 흔적.
    agent_hint: bool = False
    labels: tuple[str, ...] = ()


@dataclass(frozen=True)
class RiskResult:
    score: int
    triggered: bool
    #: 왜 막혔는지. 점수만 던지면 개발자가 반발한다. 반드시 채운다.
    reasons: tuple[str, ...]
    #: 질문 생성에 넘길 상위 hunk. 전부 넘기면 컨텍스트가 넘치고 초점이 흐려진다.
    top_hunks: tuple[Hunk, ...]


@dataclass
class Question:
    type: QuestionType
    anchor: str
    text: str
    #: 이 답변에 반드시 포함되어야 하는 사실.
    expected_evidence: str


@dataclass
class Answer:
    anchor: str
    text: str
    verdict: Verdict | None = None
    #: 보류일 때 어디를 보면 되는지 한 문장.
    hint: str = ""


@dataclass
class Attestation:
    """인증은 PR이 아니라 커밋 하나에 묶인다. 새 커밋이 오면 무효다."""

    version: int
    repo: str
    pr: int
    head_sha: str
    actor: str
    role: Literal["author", "reviewer"]
    band: Band
    score: int
    reasons: tuple[str, ...]
    answers: list[Answer] = field(default_factory=list)
    created_at: str = ""
