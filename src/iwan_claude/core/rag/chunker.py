"""
文档分块模块 - 将各种格式的文件分割为可索引的文本块

【学习要点】
1. 文件格式识别：根据文件扩展名选择不同的分块策略
2. 代码感知分块：Python 文件使用 AST 解析，按函数/类分块
3. 文档感知分块：Markdown 文件按标题层级分块
4. 通用分块：其他格式使用滑动窗口策略，支持重叠
5. 元数据保留：每个块保留来源路径、行号、符号信息等

【支持的文件格式】
- Python (.py): AST 解析，按函数/类/方法分块
- Markdown (.md, .markdown): 按标题层级分块
- JSON (.json): 按键值对分块，支持嵌套结构
- YAML (.yaml, .yml): 解析为 JSON 后分块
- XML (.xml): 按元素和属性分块
- CSV (.csv): 按行分块，保留表头
- 其他文本文件：滑动窗口分块

【核心类】
- Chunk: 分块数据模型
- DocumentChunker: 文档分块器

【分块策略对比】
| 文件类型 | 分块策略 | 优势 |
|---------|---------|------|
| Python | AST 解析 | 保持代码语义完整性 |
| Markdown | 标题层级 | 保持文档结构完整性 |
| JSON/YAML | 键值对遍历 | 保持数据结构完整性 |
| XML | 元素遍历 | 保持 XML 结构完整性 |
| CSV | 逐行处理 | 保持表格结构完整性 |
| 其他 | 滑动窗口 | 通用，支持重叠 |

【重叠机制】
滑动窗口分块时，相邻块之间有重叠（默认 64 字符），
确保上下文连续性，提高检索准确性。
"""
from __future__ import annotations

import ast
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class Chunk(BaseModel):
    """
    分块数据模型 - 表示文档中的一个文本块

    【学习要点】
    1. 元数据完整性：每个块保留来源路径、行号、符号信息
    2. 唯一标识：使用 UUID 生成唯一的 chunk_id
    3. 灵活扩展：metadata 字段支持自定义元数据

    【字段说明】
    - text: str - 块的文本内容
    - source_path: str - 来源文件路径（绝对路径）
    - start_line: int - 起始行号（从 1 开始）
    - end_line: int - 结束行号（从 1 开始）
    - symbol: str | None - 代码符号名称（如 "class MyClass", "def my_func"）
    - section_path: list[str] | None - Markdown 章节路径（如 ["第一章", "1.1 节"]）
    - chunk_id: str - 块的唯一标识（UUID 前 12 位）
    - metadata: dict[str, Any] - 自定义元数据
    - context: str | None - Contextual Retrieval 上下文摘要
        （LLM 生成的 50-100 token 上下文，说明该块在项目中的位置和作用，
         embedding 时拼接到 text 前面以提升检索质量）
    - parent_id: str | None - 父级 Chunk 的 ID
        （Parent-Child 检索用：方法→类、段落→章节，
         检索到子 chunk 后可返回父级 chunk 提供更完整的上下文）

    【使用场景】
    - 代码文件：symbol 字段存储函数/类名，便于定位
    - 文档文件：section_path 字段存储章节路径，便于导航
    - 数据文件：metadata 字段存储额外信息，便于过滤
    - Contextual Retrieval：context 字段存储 LLM 生成的上下文摘要
    - Parent-Child：parent_id 字段记录父子关系，支持上下文扩展

    【设计目的】
    每个 Chunk 对象包含足够的上下文信息，
    在检索时可以精确定位来源，并提供丰富的元数据用于过滤。
    context 和 parent_id 字段支持高级 RAG 策略：
    - context：解决"这个函数属于哪个模块"的上下文缺失问题
    - parent_id：解决"检索到片段但周围上下文不够"的问题
    """
    text: str
    source_path: str
    start_line: int
    end_line: int
    symbol: str | None = None
    section_path: list[str] | None = None
    chunk_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    metadata: dict[str, Any] = Field(default_factory=dict)
    # Contextual Retrieval 上下文摘要（LLM 生成，embedding 时拼接到 text 前面）
    context: str | None = None
    # Parent-Child 父级 Chunk ID（方法→类、段落→章节）
    parent_id: str | None = None


@dataclass
class DocumentChunker:
    """
    文档分块器 - 根据文件格式选择不同的分块策略

    【学习要点】
    1. 策略模式：根据文件扩展名选择不同的分块策略
    2. 代码感知：Python 文件使用 AST 解析，保持代码语义完整性
    3. 文档感知：Markdown 文件按标题层级分块，保持文档结构
    4. 滑动窗口：通用文本使用滑动窗口，支持重叠
    5. 配置灵活：chunk_size 和 chunk_overlap 参数可配置

    【配置参数】
    - chunk_size: int - 块的最大字符数（默认 512）
    - chunk_overlap: int - 相邻块之间的重叠字符数（默认 64）

    【分块策略】
    - Python (.py): AST 解析，按函数/类/方法分块
    - Markdown (.md, .markdown): 按标题层级分块
    - JSON (.json): 按键值对分块
    - YAML (.yaml, .yml): 解析为 JSON 后分块
    - XML (.xml): 按元素和属性分块
    - CSV (.csv): 按行分块
    - 其他：滑动窗口分块

    【设计目的】
    针对不同文件格式采用最优的分块策略，
    既保持数据结构的完整性，又确保块大小适中，
    便于后续的向量嵌入和检索。
    """
    # 块的最大字符数（默认 512）
    chunk_size: int = 512
    # 相邻块之间的重叠字符数（默认 64）
    chunk_overlap: int = 64

    def chunk_file(self, path: Path) -> list[Chunk]:
        """
        根据文件格式选择分块策略

        【参数说明】
        - path: Path - 文件路径

        【返回值】
        - list[Chunk]: 分块结果列表

        【分块策略选择】
        1. 根据文件扩展名选择分块方法
        2. Python 文件使用 AST 解析
        3. Markdown 文件按标题层级分块
        4. JSON/YAML/XML/CSV 使用结构化分块
        5. 其他文件使用滑动窗口分块

        【执行流程】
        1. 获取文件扩展名
        2. 根据扩展名分发到对应的分块方法
        3. 返回分块结果

        【设计要点】
        - 使用 if-elif 链实现策略选择
        - 扩展新格式只需添加新的 elif 分支
        - 默认使用滑动窗口分块（兜底策略）

        【示例】
        ```python
        chunker = DocumentChunker(chunk_size=512, chunk_overlap=64)
        chunks = chunker.chunk_file(Path("src/main.py"))
        # 返回按函数/类分块的 Chunk 列表
        ```
        """
        # 获取文件扩展名（小写）
        ext = path.suffix.lower()
        
        # 根据扩展名选择分块策略
        if ext == ".py":
            return self._chunk_python(path)
        elif ext in (".md", ".markdown"):
            return self._chunk_markdown(path)
        elif ext == ".json":
            return self._chunk_json(path)
        elif ext in (".yaml", ".yml"):
            return self._chunk_yaml(path)
        elif ext == ".xml":
            return self._chunk_xml(path)
        elif ext == ".csv":
            return self._chunk_csv(path)
        else:
            # 兜底策略：使用滑动窗口分块
            return self._chunk_plaintext(path)

    def _chunk_python(self, path: Path) -> list[Chunk]:
        """
        Python 文件分块 - 使用 AST 解析，按函数/类/方法分块

        【参数说明】
        - path: Path - Python 文件路径

        【返回值】
        - list[Chunk]: 分块结果列表

        【核心技术】
        使用 Python AST（抽象语法树）解析代码结构，
        按函数、异步函数、类进行分块，保持代码语义完整性。

        【执行流程】
        1. 读取文件内容
        2. 解析为 AST
        3. 递归遍历 AST 节点
        4. 对每个函数/类创建 Chunk
        5. 如果没有找到任何符号，使用滑动窗口分块

        【AST 节点类型】
        - ast.FunctionDef: 普通函数定义
        - ast.AsyncFunctionDef: 异步函数定义
        - ast.ClassDef: 类定义

        【符号命名规则】
        - 普通函数："def func_name"
        - 异步函数："async def func_name"
        - 类："class ClassName"
        - 嵌套符号："ClassName.method_name"

        【设计目的】
        保持代码的语义完整性，每个函数/类作为一个独立的块，
        便于后续检索时精确定位代码位置。

        【注意事项】
        - 空文件或没有任何符号的文件会回退到滑动窗口分块
        - 行号从 1 开始
        """
        # 读取文件内容
        content = path.read_text(encoding="utf-8")
        # 解析为 AST（抽象语法树）
        tree = ast.parse(content)
        # 按行分割（保留换行符）
        lines = content.splitlines(keepends=True)
        chunks: list[Chunk] = []

        # 获取符号名称（函数名或类名）
        def get_symbol_name(node: ast.AST) -> str:
            if isinstance(node, ast.FunctionDef):
                return f"def {node.name}"
            elif isinstance(node, ast.AsyncFunctionDef):
                return f"async def {node.name}"
            elif isinstance(node, ast.ClassDef):
                return f"class {node.name}"
            return ""

        # 递归遍历 AST 节点
        # parent_chunk_id 参数用于 Parent-Child 关系：方法的 parent_id 指向所属类的 chunk_id
        def visit_node(
            node: ast.AST,
            parent_symbols: list[str] = [],
            parent_chunk_id: str | None = None,
        ) -> None:
            # 如果是函数或类定义
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                # 获取起始行号和结束行号
                start_line = node.lineno
                end_line = node.end_lineno or start_line
                # 获取符号名称
                symbol = get_symbol_name(node)
                # 构建完整符号路径（处理嵌套）
                full_symbol = ".".join(parent_symbols + [symbol]) if symbol else ""
                # 提取代码文本
                chunk_text = "".join(lines[start_line - 1:end_line])

                # 如果文本不为空，创建 Chunk
                # parent_id 设置为父级 Chunk 的 ID（Parent-Child：方法→类）
                if chunk_text.strip():
                    chunk = Chunk(
                        text=chunk_text,
                        source_path=str(path),
                        start_line=start_line,
                        end_line=end_line,
                        symbol=full_symbol,
                        metadata={"header_context": symbol},
                        parent_id=parent_chunk_id,
                    )
                    chunks.append(chunk)
                    # 子节点的父级 ID 为当前 Chunk 的 ID
                    child_parent_id = chunk.chunk_id
                else:
                    # 没有文本时，子节点继承当前父级 ID
                    child_parent_id = parent_chunk_id

                # 更新父符号列表（用于嵌套符号）
                new_parent = parent_symbols + ([symbol] if symbol else [])
                # 递归处理子节点，传入当前 Chunk 的 ID 作为子节点的 parent_id
                for child in ast.iter_child_nodes(node):
                    visit_node(child, new_parent, child_parent_id)
            else:
                # 非函数/类节点，递归处理子节点（继承 parent_id）
                for child in ast.iter_child_nodes(node):
                    visit_node(child, parent_symbols, parent_chunk_id)

        # 开始遍历 AST
        visit_node(tree)

        # 如果没有找到任何符号，回退到滑动窗口分块
        if not chunks:
            chunks = self._chunk_plaintext_lines(lines, str(path))

        return chunks

    def _chunk_markdown(self, path: Path) -> list[Chunk]:
        """
        Markdown 文件分块 - 按标题层级分块

        【参数说明】
        - path: Path - Markdown 文件路径

        【返回值】
        - list[Chunk]: 分块结果列表

        【核心技术】
        使用正则表达式匹配 Markdown 标题，
        按标题层级进行分块，保持文档结构完整性。

        【标题匹配规则】
        正则表达式: ^(#+)\s+(.*)$
        - 捕获组 1: 标题级别（# 的数量）
        - 捕获组 2: 标题文本

        【章节路径管理】
        使用列表维护当前章节路径，
        遇到新标题时，根据级别更新路径：
        - 级别增加：追加到路径
        - 级别不变：替换同级
        - 级别减少：截断到对应级别

        【执行流程】
        1. 读取文件内容
        2. 按行分割
        3. 遍历每行，匹配标题
        4. 遇到标题时，将当前累积的文本创建为 Chunk
        5. 更新章节路径和当前文本缓冲区
        6. 处理最后一段文本

        【设计目的】
        保持文档的章节结构完整性，每个章节作为一个独立的块，
        section_path 字段记录完整的章节路径，便于导航和过滤。

        【注意事项】
        - 文件开头到第一个标题之间的内容作为第一个块
        - 没有任何标题的文件作为一个块
        """
        # 读取文件内容
        content = path.read_text(encoding="utf-8")
        # 按行分割（保留换行符）
        lines = content.splitlines(keepends=True)
        chunks: list[Chunk] = []
        # 当前章节路径（如 ["第一章", "1.1 节"]）
        current_section: list[str] = []
        # 当前块的文本行
        current_lines: list[str] = []
        # 当前块的起始行号
        current_start_line = 1
        # 每个层级当前章节的 chunk_id（用于 Parent-Child 关系：子章节→父章节）
        section_chunk_ids: dict[int, str] = {}

        # 标题正则表达式：匹配 # 开头的行
        header_pattern = re.compile(r"^(#+)\s+(.*)$")

        # 遍历每行
        for i, line in enumerate(lines, start=1):
            # 匹配标题
            match = header_pattern.match(line)
            if match:
                # 如果当前有累积的文本，创建 Chunk
                if current_lines:
                    chunk_text = "".join(current_lines)
                    if chunk_text.strip():
                        # parent_id 为上级章节的 chunk_id（Parent-Child：子章节→父章节）
                        section_depth = len(current_section)
                        parent_id = section_chunk_ids.get(section_depth - 1) if section_depth > 0 else None
                        chunk = Chunk(
                            text=chunk_text,
                            source_path=str(path),
                            start_line=current_start_line,
                            end_line=i - 1,
                            section_path=list(current_section),
                            metadata={"header_context": "/".join(current_section)},
                            parent_id=parent_id,
                        )
                        chunks.append(chunk)
                        # 记录当前层级章节的 chunk_id，供子章节引用
                        if section_depth > 0:
                            section_chunk_ids[section_depth] = chunk.chunk_id

                # 获取标题级别（# 的数量）
                level = len(match.group(1))
                # 获取标题文本
                title = match.group(2).strip()

                # 更新章节路径
                # 截断到 level-1 级，然后追加新标题
                current_section = current_section[:level - 1] + [title]
                # 重置当前文本缓冲区，包含当前标题行
                current_lines = [line]
                # 更新起始行号
                current_start_line = i
            else:
                # 非标题行，添加到当前文本缓冲区
                current_lines.append(line)

        # 处理最后一段文本
        if current_lines:
            chunk_text = "".join(current_lines)
            if chunk_text.strip():
                # parent_id 为上级章节的 chunk_id
                section_depth = len(current_section)
                parent_id = section_chunk_ids.get(section_depth - 1) if section_depth > 0 else None
                chunk = Chunk(
                    text=chunk_text,
                    source_path=str(path),
                    start_line=current_start_line,
                    end_line=len(lines),
                    section_path=list(current_section),
                    metadata={"header_context": "/".join(current_section)},
                    parent_id=parent_id,
                )
                chunks.append(chunk)

        return chunks

    def _chunk_plaintext(self, path: Path) -> list[Chunk]:
        """
        纯文本文件分块 - 使用滑动窗口策略

        【参数说明】
        - path: Path - 文件路径

        【返回值】
        - list[Chunk]: 分块结果列表

        【执行流程】
        1. 读取文件内容
        2. 按行分割
        3. 调用 _chunk_plaintext_lines 进行滑动窗口分块

        【设计目的】
        作为兜底策略，处理所有未识别的文件格式。
        """
        # 读取文件内容
        content = path.read_text(encoding="utf-8")
        # 按行分割（保留换行符）
        lines = content.splitlines(keepends=True)
        # 调用滑动窗口分块方法
        return self._chunk_plaintext_lines(lines, str(path))

    def _chunk_plaintext_lines(self, lines: list[str], source_path: str) -> list[Chunk]:
        """
        滑动窗口分块 - 核心分块算法

        【参数说明】
        - lines: list[str] - 文本行列表（保留换行符）
        - source_path: str - 来源文件路径

        【返回值】
        - list[Chunk]: 分块结果列表

        【核心算法】滑动窗口 + 重叠机制
        1. 累积文本直到超过 chunk_size
        2. 创建 Chunk，包含累积的文本
        3. 保留最后 chunk_overlap 个字符作为重叠
        4. 继续累积新文本

        【重叠机制】
        相邻块之间有重叠（默认 64 字符），
        确保上下文连续性，提高检索准确性。

        【执行流程】
        1. 初始化当前文本缓冲区和起始行号
        2. 遍历每行：
           - 尝试添加到当前缓冲区
           - 如果超过 chunk_size，创建 Chunk
           - 保留重叠部分，继续累积
        3. 处理最后一段文本

        【设计目的】
        通用分块策略，适用于所有文本格式，
        支持配置块大小和重叠大小。

        【注意事项】
        - 行号从 1 开始
        - 空文件返回空列表
        - 单个行超过 chunk_size 时会被拆分为多个块
        """
        chunks: list[Chunk] = []
        # 当前块的文本
        current_text = ""
        # 当前块的起始行号
        current_start_line = 1

        # 遍历每行
        for i, line in enumerate(lines, start=1):
            # 尝试添加当前行到缓冲区
            temp = current_text + line
            # 如果超过块大小且缓冲区不为空
            if len(temp) > self.chunk_size and current_text:
                # 创建 Chunk
                chunks.append(Chunk(
                    text=current_text,
                    source_path=source_path,
                    start_line=current_start_line,
                    end_line=i - 1,
                ))

                # 计算重叠大小（不超过当前文本长度）
                overlap_size = min(self.chunk_overlap, len(current_text))
                # 保留重叠部分 + 当前行
                current_text = current_text[-overlap_size:] + line
                # 更新起始行号（重叠部分的起始行）
                current_start_line = i - 1
            else:
                # 继续累积
                current_text = temp

        # 处理最后一段文本
        if current_text.strip():
            chunks.append(Chunk(
                text=current_text,
                source_path=source_path,
                start_line=current_start_line,
                end_line=len(lines),
            ))

        return chunks

    def _chunk_json(self, path: Path) -> list[Chunk]:
        """
        JSON 文件分块 - 按键值对分块

        【参数说明】
        - path: Path - JSON 文件路径

        【返回值】
        - list[Chunk]: 分块结果列表

        【执行流程】
        1. 读取文件内容
        2. 解析 JSON
        3. 如果解析成功，调用 _chunk_json_data 递归分块
        4. 如果解析失败，回退到滑动窗口分块

        【设计目的】
        保持 JSON 数据结构的完整性，每个键值对作为一个块，
        便于检索时精确定位数据字段。

        【注意事项】
        - 无效 JSON 会回退到滑动窗口分块
        - 嵌套深度限制为 3 层（防止递归过深）
        """
        # 延迟导入 json 模块（避免模块加载时的循环依赖）
        import json

        # 读取文件内容
        content = path.read_text(encoding="utf-8")
        # 按行分割（保留换行符）
        lines = content.splitlines(keepends=True)

        try:
            # 解析 JSON
            data = json.loads(content)
            # 递归分块
            return self._chunk_json_data(data, str(path), lines)
        except json.JSONDecodeError:
            # 解析失败，回退到滑动窗口分块
            return self._chunk_plaintext_lines(lines, str(path))

    def _chunk_json_data(
        self, data: Any, source_path: str, lines: list[str],
        parent_key: str = "", depth: int = 0
    ) -> list[Chunk]:
        """
        JSON 数据递归分块 - 核心递归算法

        【参数说明】
        - data: Any - 当前 JSON 数据（dict 或 list）
        - source_path: str - 来源文件路径
        - lines: list[str] - 文本行列表（保留换行符）
        - parent_key: str - 父键路径（如 "config.database"）
        - depth: int - 当前递归深度

        【返回值】
        - list[Chunk]: 分块结果列表

        【核心算法】递归遍历
        1. 如果是 dict：遍历键值对
           - 值是 dict/list：递归调用
           - 值是基本类型：创建 Chunk
        2. 如果是 list：遍历元素
           - 元素是 dict/list：递归调用
           - 元素是基本类型：创建 Chunk

        【深度限制】
        max_depth = 3，防止递归过深导致性能问题

        【键路径格式】
        - dict: "parent.key"
        - list: "parent[0]"
        - 嵌套: "config.database.host"

        【设计目的】
        将 JSON 结构扁平化，每个叶子节点作为一个块，
        便于检索时精确定位数据字段。

        【注意事项】
        - 行号设置为 0（JSON 结构不按行定位）
        - 大字段会被跳过（超过 chunk_size）
        """
        # 延迟导入 json 模块
        import json

        chunks: list[Chunk] = []
        # 最大递归深度（防止递归过深）
        max_depth = 3

        # 如果超过最大深度，返回空列表
        if depth > max_depth:
            return chunks

        # 如果是字典
        if isinstance(data, dict):
            for key, value in data.items():
                # 构建当前键路径
                current_key = f"{parent_key}.{key}" if parent_key else key
                # 如果值是嵌套结构，递归处理
                if isinstance(value, (dict, list)):
                    chunks.extend(self._chunk_json_data(value, source_path, lines, current_key, depth + 1))
                else:
                    # 如果值是基本类型，创建 Chunk
                    chunk_text = f"{current_key}: {json.dumps(value, ensure_ascii=False)}"
                    # 检查块大小
                    if len(chunk_text) <= self.chunk_size:
                        chunks.append(Chunk(
                            text=chunk_text,
                            source_path=source_path,
                            start_line=0,
                            end_line=0,
                            metadata={"json_key": current_key},
                        ))
        # 如果是列表
        elif isinstance(data, list):
            for i, item in enumerate(data):
                # 构建当前键路径（带索引）
                current_key = f"{parent_key}[{i}]"
                # 如果元素是嵌套结构，递归处理
                if isinstance(item, (dict, list)):
                    chunks.extend(self._chunk_json_data(item, source_path, lines, current_key, depth + 1))
                else:
                    # 如果元素是基本类型，创建 Chunk
                    chunk_text = f"{current_key}: {json.dumps(item, ensure_ascii=False)}"
                    # 检查块大小
                    if len(chunk_text) <= self.chunk_size:
                        chunks.append(Chunk(
                            text=chunk_text,
                            source_path=source_path,
                            start_line=0,
                            end_line=0,
                            metadata={"json_key": current_key},
                        ))

        return chunks

    def _chunk_yaml(self, path: Path) -> list[Chunk]:
        content = path.read_text(encoding="utf-8")
        lines = content.splitlines(keepends=True)

        try:
            import yaml

            data = yaml.safe_load(content)
            import json

            return self._chunk_json_data(data, str(path), lines)
        except Exception:
            return self._chunk_plaintext_lines(lines, str(path))

    def _chunk_xml(self, path: Path) -> list[Chunk]:
        content = path.read_text(encoding="utf-8")
        lines = content.splitlines(keepends=True)

        try:
            import xml.etree.ElementTree as ET

            tree = ET.ElementTree(ET.fromstring(content))
            root = tree.getroot()
            chunks = self._chunk_xml_element(root, str(path), lines)
            return chunks
        except Exception:
            return self._chunk_plaintext_lines(lines, str(path))

    def _chunk_xml_element(
        self, elem: Any, source_path: str, lines: list[str],
        parent_tag: str = "", depth: int = 0
    ) -> list[Chunk]:
        chunks: list[Chunk] = []
        max_depth = 3

        if depth > max_depth:
            return chunks

        tag = elem.tag
        current_path = f"{parent_tag}/{tag}" if parent_tag else tag

        if elem.text and elem.text.strip():
            chunk_text = f"<{tag}>{elem.text.strip()}</{tag}>"
            chunks.append(Chunk(
                text=chunk_text,
                source_path=source_path,
                start_line=0,
                end_line=0,
                metadata={"xml_path": current_path},
            ))

        for child in elem:
            chunks.extend(self._chunk_xml_element(child, source_path, lines, current_path, depth + 1))

        for attr_name, attr_value in elem.attrib.items():
            chunk_text = f"{current_path}[{attr_name}=\"{attr_value}\"]"
            chunks.append(Chunk(
                text=chunk_text,
                source_path=source_path,
                start_line=0,
                end_line=0,
                metadata={"xml_attr": f"{current_path}.{attr_name}"},
            ))

        return chunks

    def _chunk_csv(self, path: Path) -> list[Chunk]:
        import csv

        content = path.read_text(encoding="utf-8")
        lines = content.splitlines(keepends=True)

        try:
            reader = csv.DictReader(lines)
            if not reader.fieldnames:
                return self._chunk_plaintext_lines(lines, str(path))

            chunks: list[Chunk] = []
            header = ", ".join(reader.fieldnames)
            chunk_text = f"CSV Header: {header}"
            chunks.append(Chunk(
                text=chunk_text,
                source_path=str(path),
                start_line=1,
                end_line=1,
                metadata={"csv_type": "header"},
            ))

            for row_num, row in enumerate(reader, start=2):
                row_text = ", ".join(f"{k}={v}" for k, v in row.items())
                if len(row_text) <= self.chunk_size:
                    chunks.append(Chunk(
                        text=row_text,
                        source_path=str(path),
                        start_line=row_num,
                        end_line=row_num,
                        metadata={"csv_type": "row", "row_number": row_num},
                    ))

            return chunks
        except Exception:
            return self._chunk_plaintext_lines(lines, str(path))
