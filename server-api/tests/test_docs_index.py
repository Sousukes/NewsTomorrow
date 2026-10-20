"""F4：文档索引的链接完整性。

拆分文档（把四个巨型文件拆成 60+ 篇）最典型的失败是**链接断裂**：
文件名改了一个字，所有指向它的链接静默失效，markdown 渲染成普通文本，
没有任何报错。所以这里把"索引里的每个链接都指向真实文件"钉成断言。

同时守住两条约定：
- **索引必须完整**：落地记录目录里的每个批次文件都要被索引引用
  （漏挂一个 = 那个批次从此不可检索）；
- **主文档必须保持瘦身**：超过阈值就说明又有人在往里堆内容了。

运行：
    cd server-api
    pytest tests/test_docs_index.py -q
"""
import os
import re
import sys
from pathlib import Path
from urllib.parse import unquote

import pytest

os.environ["DATABASE_URL"] = "sqlite:///:memory:"

REPO = Path(__file__).resolve().parents[2]
DOCS = REPO / "docs"

#: 主文档（入口/索引）超过这个体积就说明该继续拆了。
#: 拆分前：后端 README 137KB、落地记录 217KB、前端 README 68KB、速查表 137KB
SLIM_LIMIT_BYTES = 40 * 1024

LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
EXTERNAL_PREFIXES = ("http://", "https://", "mailto:", "#")

ENTRY_DOCS = [
    DOCS / "README.md",
    REPO / "明日头条-组件与接口速查表.md",
    REPO / "server-api" / "README.md",
    REPO / "mingri-toutiao-vue" / "README.md",
]

#: 本地治理台账。**已从版本库移除**（见 `.gitignore`）：它是过程资料，
#: 公开仓库里没有读者。本地存在时才校验——否则新克隆的 CI 会因为
#: "文件不存在"而红，而那与代码质量无关。
LOCAL_LEDGER = REPO / "优化落地记录.md"
HAS_LOCAL_LEDGER = LOCAL_LEDGER.exists()

#: 只存在于工作区、不随仓库分发的文档（按文件名匹配，链接可能带相对路径）
LOCAL_ONLY_DOCS = {"优化落地记录.md", "项目评估与优化建议.md"}


def _links(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    return [
        m.group(1).strip()
        for m in LINK_RE.finditer(text)
        if not m.group(1).strip().startswith(EXTERNAL_PREFIXES)
    ]


class TestLinksResolve:
    @pytest.mark.parametrize("doc", ENTRY_DOCS, ids=lambda p: p.name)
    def test_entry_doc_exists(self, doc: Path):
        assert doc.exists(), f"入口文档缺失：{doc}"

    def test_all_internal_links_resolve(self):
        """遍历全部 markdown，内部链接必须指向真实存在的文件。"""
        broken: list[str] = []
        for md in REPO.rglob("*.md"):
            if any(part in {"node_modules", ".git", ".tmp", "dist"} for part in md.parts):
                continue
            for raw in _links(md):
                target = unquote(raw.split("#", 1)[0].strip())
                if not target:
                    continue
                # 指向"本地治理文档"的链接在**新克隆里必然悬空**：那些文件
                # 已从版本库移除（见 `.gitignore`）。它们不是内容文档，
                # 不参与公开仓库的链接完整性——本地存在时上面已经校验过。
                if Path(target).name in LOCAL_ONLY_DOCS:
                    continue
                if not (md.parent / target).exists():
                    broken.append(f"{md.relative_to(REPO)} → {raw}")
        assert not broken, "断链（拆分后最容易出的错，且没有任何报错）：\n" + "\n".join(broken)

    def test_doc_index_lists_every_batch_file(self):
        """落地记录目录里的每个批次文件都要被索引引用。

        漏挂一个 = 那个批次从此不可检索，而目录里明明有它——这种不一致
        不会触发任何错误。
        """
        if not HAS_LOCAL_LEDGER:
            pytest.skip("本地治理台账（优化落地记录.md）不在版本库中，跳过索引覆盖检查")
        batch_dir = DOCS / "治理" / "落地记录"
        files = {f.name for f in batch_dir.glob("*.md")}
        assert len(files) >= 40, "批次文件数量异常，拆分可能被误删"
        index = LOCAL_LEDGER.read_text(encoding="utf-8")
        missing = sorted(f for f in files if f not in index)
        assert not missing, f"这些批次文件没被索引引用：{missing}"

    def test_docs_index_covers_each_topic_dir(self):
        """总索引要覆盖每个主题目录，且链接可用（复用上面的解析逻辑）。"""
        index = _links(DOCS / "README.md")
        for topic in ("后端", "前端", "速查表"):
            assert any(f.startswith(topic) or f"/{topic}/" in f for f in index), f"总索引没指向 {topic}"
        assert any("deploy.md" in f for f in index), "总索引没指向部署文档"


class TestEntryDocsStaySlim:
    @pytest.mark.parametrize("doc", ENTRY_DOCS, ids=lambda p: p.name)
    def test_entry_doc_size(self, doc: Path):
        size = doc.stat().st_size
        assert size < SLIM_LIMIT_BYTES, (
            f"{doc.relative_to(REPO)} 涨到 {size // 1024}KB（阈值 "
            f"{SLIM_LIMIT_BYTES // 1024}KB）。入口文档只放索引与最常用的内容，"
            f"细节请写进 docs/ 对应专题——这正是 F4 拆分要解决的问题。"
        )

    @pytest.mark.parametrize("doc", ENTRY_DOCS, ids=lambda p: p.name)
    def test_entry_doc_points_into_docs(self, doc: Path):
        """入口文档必须给出深入阅读的路径，否则拆分等于把内容藏起来。"""
        if doc == DOCS / "README.md":
            return  # 它本身就是索引
        assert _links(doc), f"{doc.name} 没有任何内部链接：读者无法从入口走到详情"


@pytest.mark.skipif(sys.platform == "win32", reason="仅在 CI 上做全量扫描的冒烟检查")
def test_markdown_scan_smoke():
    """占位：全量扫描在上面的用例里已经做了，这里保证扫描路径本身可运行。"""
    md_count = len([p for p in REPO.rglob("*.md") if "node_modules" not in p.parts])
    assert md_count > 40


#: 任何单篇文档的体积上限（含 review 下的历史报告）。
#: 定在 60KB 是因为拆分后最大的一篇是 45KB（RSS 抓取），
#: 留出约三成余量；原形态是 137~217KB —— 那已经无法检索了。
ANY_DOC_LIMIT_BYTES = 60 * 1024


def test_no_doc_file_grows_back_to_unsearchable():
    """防止"拆完又长回去"。

    这是本次拆分的真正目的：不是文件变好看了，而是能搜了。
    只盯入口文件是不够的——细节文档同样会被人继续往里塞。
    """
    oversized = []
    for md in REPO.rglob("*.md"):
        if any(part in {"node_modules", ".git", ".tmp", "dist"} for part in md.parts):
            continue
        size = md.stat().st_size
        if size > ANY_DOC_LIMIT_BYTES:
            oversized.append(f"{md.relative_to(REPO)} ({size // 1024}KB)")
    assert not oversized, (
        "以下文档超过 "
        f"{ANY_DOC_LIMIT_BYTES // 1024}KB，该拆了：{oversized}"
    )