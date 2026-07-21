"""
内置工具模块 - 所有内置工具的统一导出入口

【学习要点】
1. 模块聚合模式：将分散在多个文件中的工具类统一在此处导出
2. __all__ 控制公开接口：明确列出所有可被外部导入的工具类
3. 工具分类组织：按功能类别分组导入，便于维护和查找

【工具分类】
- 文件操作：ReadFileTool, WriteFileTool, ListDirTool, fs_ops 中的工具
- 文本编辑：editor 中的工具（ViewFileTool, EditByLinesTool 等）
- 搜索：FindFilesTool, GrepSearchTool
- Git：GitStatusTool, GitDiffTool, GitCommitTool, GitLogTool, GitCheckoutTool
- 代码质量：LintCodeTool, ReviewCodeTool, SecurityScanTool
- 依赖管理：PipManageTool, DependencyCheckTool
- 文档：GenerateDocsTool, UpdateReadmeTool, ChangelogTool
- 缓存：CacheGetTool, CacheSetTool, CacheDeleteTool, CacheInvalidateTool, CacheStatsTool
- 协作：AssignRoleTool, ListRolesTool, ShareKnowledgeTool
- HTTP：HttpRequestTool
- 系统：ProcessListTool, BashTool
- Checkpoint：ListCheckpointsTool, RestoreCheckpointTool
- Skill：SkillListTool, SkillInstallTool, SkillCreateTool, SkillDeleteTool, SkillInfoTool
- 任务管理：TaskCreateTool, TaskListTool, TaskGetTool, TaskUpdateTool
- 其他：AddContextTool, NoteSaveTool, RunPythonTool

【使用示例】
```python
from iwan_claude.core.tools.builtin import ReadFileTool, WriteFileTool

# 注册工具到注册表
registry = ToolRegistry()
registry.register(ReadFileTool())
registry.register(WriteFileTool())
```
"""
from iwan_claude.core.tools.builtin.bash import BashTool
from iwan_claude.core.tools.builtin.editor import (
    DeleteLinesTool,
    EditByLinesTool,
    EditBySearchTool,
    InsertAtLineTool,
    ViewFileTool,
)
from iwan_claude.core.tools.builtin.fs_ops import (
    CopyFileTool,
    DeleteFileTool,
    FileExistsTool,
    FileStatTool,
    MkdirTool,
    RenameFileTool,
)
from iwan_claude.core.tools.builtin.context import AddContextTool
from iwan_claude.core.tools.builtin.git import (
    GitCheckoutTool,
    GitCommitTool,
    GitDiffTool,
    GitLogTool,
    GitStatusTool,
)
from iwan_claude.core.tools.builtin.code_quality import (
    LintCodeTool,
    ReviewCodeTool,
    SecurityScanTool,
)
from iwan_claude.core.tools.builtin.dependency import DependencyCheckTool, PipManageTool
from iwan_claude.core.tools.builtin.documentation import (
    ChangelogTool,
    GenerateDocsTool,
    UpdateReadmeTool,
)
from iwan_claude.core.tools.builtin.cache import (
    CacheDeleteTool,
    CacheGetTool,
    CacheInvalidateTool,
    CacheSetTool,
    CacheStatsTool,
)
from iwan_claude.core.tools.builtin.collaboration import (
    AssignRoleTool,
    ListRolesTool,
    ShareKnowledgeTool,
)
from iwan_claude.core.tools.builtin.http import HttpRequestTool
from iwan_claude.core.tools.builtin.list_dir import ListDirTool
from iwan_claude.core.tools.builtin.note_save import NoteSaveTool
from iwan_claude.core.tools.builtin.read_file import ReadFileTool
from iwan_claude.core.tools.builtin.run_python import RunPythonTool
from iwan_claude.core.tools.builtin.search import FindFilesTool, GrepSearchTool
from iwan_claude.core.tools.builtin.skill import (
    SkillCreateTool,
    SkillDeleteTool,
    SkillInfoTool,
    SkillInstallTool,
    SkillListTool,
)
from iwan_claude.core.tools.builtin.system import ProcessListTool
from iwan_claude.core.tools.builtin.checkpoint import ListCheckpointsTool, RestoreCheckpointTool
from iwan_claude.core.tools.builtin.task_create import TaskCreateTool
from iwan_claude.core.tools.builtin.task_get import TaskGetTool
from iwan_claude.core.tools.builtin.task_list import TaskListTool
from iwan_claude.core.tools.builtin.task_update import TaskUpdateTool
from iwan_claude.core.tools.builtin.write_file import WriteFileTool

__all__ = [
    "BashTool",
    "CopyFileTool",
    "DeleteFileTool",
    "DeleteLinesTool",
    "EditByLinesTool",
    "EditBySearchTool",
    "FileExistsTool",
    "FileStatTool",
    "FindFilesTool",
    "GitCheckoutTool",
    "GitCommitTool",
    "GitDiffTool",
    "GitLogTool",
    "GitStatusTool",
    "GrepSearchTool",
    "HttpRequestTool",
    "AddContextTool",
    "LintCodeTool",
    "ReviewCodeTool",
    "SecurityScanTool",
    "PipManageTool",
    "DependencyCheckTool",
    "GenerateDocsTool",
    "UpdateReadmeTool",
    "ChangelogTool",
    "CacheGetTool",
    "CacheSetTool",
    "CacheDeleteTool",
    "CacheInvalidateTool",
    "CacheStatsTool",
    "AssignRoleTool",
    "ListRolesTool",
    "ShareKnowledgeTool",
    "InsertAtLineTool",
    "ListDirTool",
    "MkdirTool",
    "NoteSaveTool",
    "ProcessListTool",
    "ListCheckpointsTool",
    "RestoreCheckpointTool",
    "ReadFileTool",
    "RenameFileTool",
    "RunPythonTool",
    "SkillCreateTool",
    "SkillDeleteTool",
    "SkillInfoTool",
    "SkillInstallTool",
    "SkillListTool",
    "TaskCreateTool",
    "TaskGetTool",
    "TaskListTool",
    "TaskUpdateTool",
    "ViewFileTool",
    "WriteFileTool",
]
