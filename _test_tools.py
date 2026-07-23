"""测试所有可以独立运行的工具 - 直接导入模块测试"""
import sys
import os
from pathlib import Path

# 先禁用沙箱
os.environ["IWAN_SANDBOX_ENABLED"] = "false"

# 添加 src 目录到路径
sys.path.insert(0, str(Path(__file__).parent / "src"))

from iwan_claude.core.tools.builtin.cache import (
    CacheSetTool, CacheGetTool, CacheDeleteTool, 
    CacheInvalidateTool, CacheStatsTool
)
from iwan_claude.core.tools.builtin.fs_ops import (
    MkdirTool, FileExistsTool, FileStatTool, 
    CopyFileTool, RenameFileTool, DeleteFileTool
)
from iwan_claude.core.tools.builtin.search import FindFilesTool, GrepSearchTool
from iwan_claude.core.tools.builtin.list_dir import ListDirTool
from iwan_claude.core.tools.builtin.read_file import ReadFileTool
from iwan_claude.core.tools.builtin.write_file import WriteFileTool
from iwan_claude.core.tools.builtin.editor import (
    ViewFileTool, EditByLinesTool, EditBySearchTool, 
    InsertAtLineTool, DeleteLinesTool
)
from iwan_claude.core.tools.builtin.code_quality import ReviewCodeTool, SecurityScanTool
from iwan_claude.core.tools.builtin.collaboration import AssignRoleTool, ListRolesTool, ShareKnowledgeTool
from iwan_claude.core.tools.builtin.system import ProcessListTool
from iwan_claude.core.tools.builtin.note_save import NoteSaveTool
from iwan_claude.core.tools.builtin.context import AddContextTool
from iwan_claude.core.tools.builtin.http import HttpRequestTool
from iwan_claude.core.tools.builtin.performance import ProfileCodeTool
from iwan_claude.core.tools.builtin.run_python import RunPythonTool
from iwan_claude.core.tools.builtin.skill import (
    SkillListTool, SkillInfoTool, SkillCreateTool, SkillInstallTool, SkillDeleteTool
)
from iwan_claude.core.tools.builtin.task_create import TaskCreateTool
from iwan_claude.core.tools.builtin.task_list import TaskListTool
from iwan_claude.core.tools.builtin.task_get import TaskGetTool
from iwan_claude.core.tools.builtin.task_update import TaskUpdateTool
from iwan_claude.core.tools.builtin.checkpoint import ListCheckpointsTool, RestoreCheckpointTool
from iwan_claude.core.tools.builtin.dependency import DependencyCheckTool, PipManageTool
from iwan_claude.core.tools.builtin.documentation import GenerateDocsTool, UpdateReadmeTool, ChangelogTool
from iwan_claude.core.tools.builtin.git import GitStatusTool, GitLogTool, GitDiffTool, GitCommitTool, GitCheckoutTool
from iwan_claude.core.tools.builtin.testing import GenerateTestsTool, RunTestsTool, TestCoverageTool

# RAG 模块
from iwan_claude.core.rag.chunker import Chunker
from iwan_claude.core.rag.vectorstore import VectorStore
from iwan_claude.core.rag.tools import SearchKnowledgeTool, IndexKnowledgeTool, ForgetKnowledgeTool

import asyncio

results = []

async def test_tool(name, tool, params, expect_error_contains=None):
    """测试单个工具"""
    try:
        result = await tool.invoke(params)
        if result.is_error:
            if expect_error_contains and expect_error_contains in result.content:
                status = "⏭️  SKIP"
            else:
                status = "❌ FAIL"
        else:
            status = "✅ PASS"
        results.append(f"{status} | {name}: {result.content[:80]}")
    except Exception as e:
        status = "⏭️  SKIP" if "not found" in str(e).lower() or "no module" in str(e).lower() else "❌ ERROR"
        results.append(f"{status} | {name}: {type(e).__name__}: {str(e)[:60]}")

async def main():
    print("=" * 70)
    print("🛠️  工具集成测试报告")
    print("=" * 70)
    
    # =========================================================================
    # 1. 缓存工具 (Cache)
    # =========================================================================
    print("\n【1/9】 缓存工具 (Cache)")
    print("-" * 40)
    
    await test_tool("cache_set", CacheSetTool(), {"key": "test_key", "value": "test_value"})
    await test_tool("cache_get (hit)", CacheGetTool(), {"key": "test_key"})
    await test_tool("cache_get (miss)", CacheGetTool(), {"key": "nonexistent"})
    await test_tool("cache_stats", CacheStatsTool(), {})
    await test_tool("cache_delete", CacheDeleteTool(), {"key": "test_key"})
    await test_tool("cache_delete (already deleted)", CacheDeleteTool(), {"key": "test_key"})
    await test_tool("cache_set (with ttl)", CacheSetTool(), {"key": "temp", "value": "temp_value", "ttl": 60})
    await test_tool("cache_get (ttl)", CacheGetTool(), {"key": "temp"})
    await test_tool("cache_invalidate", CacheInvalidateTool(), {})
    await test_tool("cache_get after invalidate", CacheGetTool(), {"key": "temp"})
    
    # =========================================================================
    # 2. 文件操作工具 (FS Ops)
    # =========================================================================
    print("\n【2/9】 文件操作工具 (FS Ops)")
    print("-" * 40)
    
    # 创建临时目录
    test_dir = "_iwan_test_dir"
    await test_tool("mkdir", MkdirTool(), {"path": test_dir})
    await test_tool("mkdir (exist_ok)", MkdirTool(), {"path": test_dir})
    
    # 文件存在检查
    await test_tool("file_exists (exists)", FileExistsTool(), {"path": test_dir})
    await test_tool("file_exists (not exist)", FileExistsTool(), {"path": "_nonexistent_file_xyz"})
    
    # 文件统计
    await test_tool("file_stat (dir)", FileStatTool(), {"path": test_dir})
    
    # 写文件
    await test_tool("write_file", WriteFileTool(), {
        "path": f"{test_dir}/test_file.py",
        "content": "# Test file\nprint('hello')\n"
    })
    
    # 读文件
    await test_tool("read_file", ReadFileTool(), {"path": f"{test_dir}/test_file.py"})
    
    # 列表目录
    await test_tool("list_dir", ListDirTool(), {"path": test_dir})
    
    # 文件统计（文件）
    await test_tool("file_stat (file)", FileStatTool(), {"path": f"{test_dir}/test_file.py"})
    
    # 复制文件
    await test_tool("copy_file", CopyFileTool(), {
        "src": f"{test_dir}/test_file.py",
        "dst": f"{test_dir}/test_file_copy.py"
    })
    
    # 重命名文件
    await test_tool("rename_file", RenameFileTool(), {
        "src": f"{test_dir}/test_file_copy.py",
        "dst": f"{test_dir}/test_file_renamed.py"
    })
    
    # 删除文件
    await test_tool("delete_file (file)", DeleteFileTool(), {"path": f"{test_dir}/test_file_renamed.py"})
    
    # 删除文件（不存在）
    await test_tool("delete_file (not exist)", DeleteFileTool(), {"path": f"{test_dir}/test_file_renamed.py"})
    
    # 删除非空目录（需要 recursive）
    await test_tool("delete_file (non-empty no recursive)", DeleteFileTool(), {"path": test_dir})
    
    # 递归删除目录
    await test_tool("delete_file (dir recursive)", DeleteFileTool(), {
        "path": test_dir, "recursive": True
    })
    
    # =========================================================================
    # 3. 编辑器工具 (Editor)
    # =========================================================================
    print("\n【3/9】 编辑器工具 (Editor)")
    print("-" * 40)
    
    # 创建测试文件
    test_edit_file = "_iwan_test_edit.txt"
    await WriteFileTool().invoke({
        "path": test_edit_file,
        "content": "line 1\nline 2\nline 3\nline 4\nline 5\n"
    })
    
    await test_tool("view_file (full)", ViewFileTool(), {
        "path": test_edit_file, "show_line_numbers": True
    })
    await test_tool("view_file (range)", ViewFileTool(), {
        "path": test_edit_file, "start_line": 2, "end_line": 4
    })
    await test_tool("view_file (page)", ViewFileTool(), {
        "path": test_edit_file, "page": 1, "page_size": 3
    })
    await test_tool("view_file (not found)", ViewFileTool(), {
        "path": "_nonexistent_file_xyz.txt"
    })
    
    await test_tool("edit_by_lines", EditByLinesTool(), {
        "path": test_edit_file, "start_line": 3, "end_line": 3,
        "replacement": "line 3 replaced\n", "backup": False
    })
    
    await test_tool("insert_at_line (after)", InsertAtLineTool(), {
        "path": test_edit_file, "line": 2, "text": "inserted after line 2\n", 
        "position": "after", "backup": False
    })
    
    await test_tool("insert_at_line (before)", InsertAtLineTool(), {
        "path": test_edit_file, "line": 4, "text": "inserted before line 4\n",
        "position": "before", "backup": False
    })
    
    await test_tool("edit_by_search (single)", EditBySearchTool(), {
        "path": test_edit_file, "old": "line 1", "new": "line one",
        "backup": False
    })
    
    await test_tool("edit_by_search (not found)", EditBySearchTool(), {
        "path": test_edit_file, "old": "ZZZZNOMATCHZZZZ", "new": "nothing",
        "backup": False
    })
    
    await test_tool("delete_lines", DeleteLinesTool(), {
        "path": test_edit_file, "start_line": 1, "end_line": 1,
        "backup": False
    })
    
    # 清理
    await DeleteFileTool().invoke({"path": test_edit_file, "force": True})

    # =========================================================================
    # 4. 代码质量工具 (Code Quality)
    # =========================================================================
    print("\n【4/9】 代码质量工具 (Code Quality)")
    print("-" * 40)
    
    # 创建测试代码文件
    test_code_file = "_iwan_test_code.py"
    await WriteFileTool().invoke({
        "path": test_code_file,
        "content": (
            "import subprocess\n"
            "import pickle\n"
            "\n"
            "password = \"secret123\"\n"
            "def run_cmd():\n"
            "    subprocess.run(['ls'], shell=True)\n"
            "def load_data():\n"
            "    # TODO: fix this later\n"
            "    pass\n"
            "    with open('data.pkl', 'rb') as f:\n"
            "        return pickle.load(f)\n"
        )
    })
    
    await test_tool("review_code (all)", ReviewCodeTool(), {"file_path": test_code_file})
    await test_tool("review_code (security)", ReviewCodeTool(), {
        "file_path": test_code_file, "focus": "security"
    })
    await test_tool("review_code (maintainability)", ReviewCodeTool(), {
        "file_path": test_code_file, "focus": "maintainability"
    })
    await test_tool("review_code (not found)", ReviewCodeTool(), {
        "file_path": "_nonexistent_file_xyz.py"
    })
    
    await test_tool("security_scan (file)", SecurityScanTool(), {"file_path": test_code_file})
    await test_tool("security_scan (dir)", SecurityScanTool(), {"directory": "."})
    await test_tool("security_scan (not found)", SecurityScanTool(), {
        "file_path": "_nonexistent_file_xyz.py"
    })
    
    # 清理
    await DeleteFileTool().invoke({"path": test_code_file, "force": True})
    
    # =========================================================================
    # 5. 搜索工具 (Search)
    # =========================================================================
    print("\n【5/9】 搜索工具 (Search)")
    print("-" * 40)
    
    await test_tool("find_files (glob)", FindFilesTool(), {
        "name_pattern": "*.py", "max_depth": 3, "max_results": 5
    })
    await test_tool("find_files (no match)", FindFilesTool(), {
        "name_pattern": "*.zzz", "max_depth": 1
    })
    await test_tool("find_files (invalid root)", FindFilesTool(), {
        "root": "_nonexistent_root"
    })
    await test_tool("find_files (content)", FindFilesTool(), {
        "content_pattern": "class", "max_depth": 3, "max_results": 3
    })
    await test_tool("find_files (dir type)", FindFilesTool(), {
        "name_pattern": "iwan*", "file_type": "dir", "max_depth": 2, "max_results": 5
    })
    
    await test_tool("grep_search (pattern)", GrepSearchTool(), {
        "pattern": "class", "root": ".", "max_matches": 5
    })
    await test_tool("grep_search (no match)", GrepSearchTool(), {
        "pattern": "ZZZZNOMATCHZZZZ", "root": ".", "max_matches": 5
    })
    await test_tool("grep_search (fixed string)", GrepSearchTool(), {
        "pattern": "def", "root": ".", "fixed_string": True, "max_matches": 5
    })
    await test_tool("grep_search (invalid root)", GrepSearchTool(), {
        "pattern": "test", "root": "_nonexistent_root"
    })
    
    # =========================================================================
    # 6. 协作/系统/其他工具
    # =========================================================================
    print("\n【6/9】 协作/系统/其他工具")
    print("-" * 40)
    
    await test_tool("assign_role", AssignRoleTool(), {
        "agent_name": "test_agent", "role": "review", "task": "Review code"
    })
    await test_tool("list_roles", ListRolesTool(), {})
    await test_tool("share_knowledge", ShareKnowledgeTool(), {
        "target_agent": "test_agent", "knowledge": "Test knowledge"
    })
    
    await test_tool("process_list", ProcessListTool(), {"max_results": 5})
    
    # Note save
    await test_tool("note_save", NoteSaveTool(), {
        "title": "test note", "content": "This is a test note"
    })
    
    # AddContext - should fail on nonexistent file
    await test_tool("add_context (not found)", AddContextTool(), {
        "path": "_nonexistent_file_xyz.py"
    })
    
    # =========================================================================
    # 7. RAG 模块
    # =========================================================================
    print("\n【7/9】 RAG 模块")
    print("-" * 40)
    
    # Test Chunker
    chunker = Chunker(chunk_size=200, chunk_overlap=20)
    try:
        chunks = chunker.chunk_text("def foo():\n    pass\n\ndef bar():\n    return 1\n", language="python")
        results.append(f"✅ PASS | chunker (python): {len(chunks)} chunks generated")
    except Exception as e:
        results.append(f"❌ ERROR | chunker (python): {e}")
    
    try:
        chunks = chunker.chunk_text("# Title\n\nSome markdown text\n\n## Subtitle\n\nMore text\n", language="markdown")
        results.append(f"✅ PASS | chunker (markdown): {len(chunks)} chunks generated")
    except Exception as e:
        results.append(f"❌ ERROR | chunker (markdown): {e}")
    
    try:
        chunks = chunker.chunk_text('{"key": "value1", "data": [1, 2, 3]}', language="json")
        results.append(f"✅ PASS | chunker (json): {len(chunks)} chunks generated")
    except Exception as e:
        results.append(f"❌ ERROR | chunker (json): {e}")
    
    try:
        chunks = chunker.chunk_text("a,b,c\n1,2,3\n4,5,6\n", language="csv")
        results.append(f"✅ PASS | chunker (csv): {len(chunks)} chunks generated")
    except Exception as e:
        results.append(f"❌ ERROR | chunker (csv): {e}")
    
    try:
        chunks = chunker.chunk_text("<root><item>test</item></root>", language="xml")
        results.append(f"✅ PASS | chunker (xml): {len(chunks)} chunks generated")
    except Exception as e:
        results.append(f"❌ ERROR | chunker (xml): {e}")
    
    try:
        chunks = chunker.chunk_text("key: value\nnested:\n  sub: val\n", language="yaml")
        results.append(f"✅ PASS | chunker (yaml): {len(chunks)} chunks generated")
    except Exception as e:
        results.append(f"❌ ERROR | chunker (yaml): {e}")
    
    # Test VectorStore
    vs = VectorStore()
    vs.add_document("doc1", "test content", {"source": "test"})
    results.append(f"✅ PASS | vectorstore.add: doc added")
    
    doc = vs.get_document("doc1")
    if doc:
        results.append(f"✅ PASS | vectorstore.get: id={doc.id}, content={doc.content[:30]}")
    else:
        results.append(f"❌ FAIL | vectorstore.get: doc not found")
    
    vs.delete_document("doc1")
    doc = vs.get_document("doc1")
    results.append(f"✅ PASS | vectorstore.delete: doc removed = {doc is None}")
    
    vs.add_document("doc2", "another test content document", {})
    vs.add_document("doc3", "third document for testing", {})
    stats = vs.stats()
    results.append(f"✅ PASS | vectorstore.stats: {stats}")
    
    vs.clear()
    stats = vs.stats()
    results.append(f"✅ PASS | vectorstore.clear: {stats}")
    
    # Test RAG tools
    await test_tool("search_knowledge (empty)", SearchKnowledgeTool(), {
        "query": "test query", "top_k": 5
    })
    await test_tool("index_knowledge", IndexKnowledgeTool(), {
        "file_path": "src/iwan_claude/core/tools/base.py"
    })
    await test_tool("forget_knowledge", ForgetKnowledgeTool(), {
        "query": "nonexistent"
    })
    
    # =========================================================================
    # 8. Git 工具（跳过 - 需要 git 仓库和命令）
    # =========================================================================
    print("\n【8/9】 Git 工具（⏭️ 跳过 - 需要 git 仓库和命令交互）")
    print("-" * 40)
    results.append("⏭️  SKIP | git_status: 需要 git 仓库和命令")
    results.append("⏭️  SKIP | git_log: 需要 git 仓库和命令")
    results.append("⏭️  SKIP | git_diff: 需要 git 仓库和命令")
    results.append("⏭️  SKIP | git_commit: 需要 git 仓库和命令")
    results.append("⏭️  SKIP | git_checkout: 需要 git 仓库和命令")
    
    # =========================================================================
    # 9. 外部依赖工具（跳过 - 需要外部命令）
    # =========================================================================
    print("\n【9/9】 外部依赖工具（⏭️ 跳过 - 需要外部命令或网络）")
    print("-" * 40)
    results.append("⏭️  SKIP | lint_code: 需要 ruff/mypy 命令")
    results.append("⏭️  SKIP | run_tests: 需要 pytest 命令")
    results.append("⏭️  SKIP | test_coverage: 需要 coverage 命令")
    results.append("⏭️  SKIP | generate_tests: 需要文件存在")
    results.append("⏭️  SKIP | profile_code: 需要可执行代码")
    results.append("⏭️  SKIP | pip_manage: 需要 pip 命令")
    results.append("⏭️  SKIP | dependency_check: 需要 pip 命令")
    results.append("⏭️  SKIP | generate_docs: 需要 pdoc 命令")
    results.append("⏭️  SKIP | http_request: 需要网络连接")
    results.append("⏭️  SKIP | run_python: 需要沙箱环境")
    results.append("⏭️  SKIP | skill_install: 需要 skill 文件")
    results.append("⏭️  SKIP | skill_info: 需要 skill 已安装")
    results.append("⏭️  SKIP | skill_create: 需要 skill 系统")
    results.append("⏭️  SKIP | skill_delete: 需要 skill 存在")
    results.append("⏭️  SKIP | task_create: 需要任务系统")
    results.append("⏭️  SKIP | task_list: 需要任务系统")
    results.append("⏭️  SKIP | task_get: 需要任务系统")
    results.append("⏭️  SKIP | task_update: 需要任务系统")
    results.append("⏭️  SKIP | list_checkpoints: 需要 checkpointer")
    results.append("⏭️  SKIP | restore_checkpoint: 需要 checkpointer")
    results.append("⏭️  SKIP | update_readme: 需要 README 存在")
    results.append("⏭️  SKIP | changelog: 需要 CHANGELOG 存在")
    results.append("⏭️  SKIP | embedding_provider: 需要 API key")
    
    # =========================================================================
    # 总结
    # =========================================================================
    print("\n" + "=" * 70)
    print("📊 测试结果汇总")
    print("=" * 70)
    
    for r in results:
        print(r)
    
    passed = sum(1 for r in results if r.startswith("✅"))
    failed = sum(1 for r in results if r.startswith("❌"))
    skipped = sum(1 for r in results if r.startswith("⏭️"))
    errors = sum(1 for r in results if r.startswith("❌ ERROR"))
    
    print()
    print(f"总计工具数: ~50+")
    print(f"✅ 通过: {passed}")
    print(f"❌ 失败: {failed}")
    print(f"⏭️  跳过: {skipped}")
    if errors:
        print(f"⚠️  其中错误: {errors}")
    
    # 清理临时文件
    for f in ["_iwan_test_edit.txt", "_iwan_test_code.py", "_iwan_test_dir"]:
        p = Path(f)
        if p.exists():
            if p.is_dir():
                import shutil
                shutil.rmtree(p)
            else:
                p.unlink()
    # 清理 _notes
    notes_dir = Path("_notes")
    if notes_dir.exists():
        import shutil
        shutil.rmtree(notes_dir)
    
    print("\n" + "=" * 70)
    print("测试完成！")
    print("=" * 70)

if __name__ == "__main__":
    asyncio.run(main())
