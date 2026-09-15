"""앵커 규칙 회귀 테스트.

앵커가 흔들리면 기존 인증의 대조가 전부 깨진다. 이 테스트가 그 방어선이다.
"""

from lasthuman.diff import make_anchor, parse_anchor, parse_hunks

RAW = """diff --git app/auth/token.py app/auth/token.py
index 1111111..2222222 100644
--- app/auth/token.py
+++ app/auth/token.py
@@ -20,6 +20,9 @@ def is_expired(token, now=None):
     return now >= token.expires_at - CLOCK_SKEW_SEC
 
+MAX_RETRIES = 3
+
+
 async def refresh(transport, token):
     body = await post_json(
diff --git migrations/002_purge.sql migrations/002_purge.sql
new file mode 100644
index 0000000..3333333
--- /dev/null
+++ migrations/002_purge.sql
@@ -0,0 +1,2 @@
+DELETE FROM orders
+WHERE deleted_at IS NOT NULL;
"""


def test_앵커는_변경_후_시작_줄을_가리킨다():
    result = parse_hunks(RAW)
    anchors = [h.anchor for h in result.hunks]
    assert anchors == ["app/auth/token.py:L20", "migrations/002_purge.sql:L1"]


def test_앵커는_왕복한다():
    assert parse_anchor(make_anchor("a/b/c.py", 88)) == ("a/b/c.py", 88)


def test_경로가_콜론을_포함해도_왕복한다():
    assert parse_anchor(make_anchor("a:b.py", 3)) == ("a:b.py", 3)


def test_새_파일은_added로_잡힌다():
    result = parse_hunks(RAW)
    statuses = {f.file: f.status for f in result.files}
    assert statuses["migrations/002_purge.sql"] == "added"
    assert statuses["app/auth/token.py"] == "modified"


def test_증감_줄_수가_집계된다():
    result = parse_hunks(RAW)
    assert result.total_additions == 5
    assert result.total_deletions == 0
