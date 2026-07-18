from __future__ import annotations

import ast
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class Chunk(BaseModel):
    text: str
    source_path: str
    start_line: int
    end_line: int
    symbol: str | None = None
    section_path: list[str] | None = None
    chunk_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    metadata: dict[str, Any] = Field(default_factory=dict)


@dataclass
class DocumentChunker:
    chunk_size: int = 512
    chunk_overlap: int = 64

    def chunk_file(self, path: Path) -> list[Chunk]:
        ext = path.suffix.lower()
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
            return self._chunk_plaintext(path)

    def _chunk_python(self, path: Path) -> list[Chunk]:
        content = path.read_text(encoding="utf-8")
        tree = ast.parse(content)
        lines = content.splitlines(keepends=True)
        chunks: list[Chunk] = []

        def get_symbol_name(node: ast.AST) -> str:
            if isinstance(node, ast.FunctionDef):
                return f"def {node.name}"
            elif isinstance(node, ast.AsyncFunctionDef):
                return f"async def {node.name}"
            elif isinstance(node, ast.ClassDef):
                return f"class {node.name}"
            return ""

        def visit_node(node: ast.AST, parent_symbols: list[str] = []) -> None:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                start_line = node.lineno
                end_line = node.end_lineno or start_line
                symbol = get_symbol_name(node)
                full_symbol = ".".join(parent_symbols + [symbol]) if symbol else ""
                chunk_text = "".join(lines[start_line - 1:end_line])

                if chunk_text.strip():
                    chunks.append(Chunk(
                        text=chunk_text,
                        source_path=str(path),
                        start_line=start_line,
                        end_line=end_line,
                        symbol=full_symbol,
                        metadata={"header_context": symbol},
                    ))

                new_parent = parent_symbols + ([symbol] if symbol else [])
                for child in ast.iter_child_nodes(node):
                    visit_node(child, new_parent)
            else:
                for child in ast.iter_child_nodes(node):
                    visit_node(child, parent_symbols)

        visit_node(tree)

        if not chunks:
            chunks = self._chunk_plaintext_lines(lines, str(path))

        return chunks

    def _chunk_markdown(self, path: Path) -> list[Chunk]:
        content = path.read_text(encoding="utf-8")
        lines = content.splitlines(keepends=True)
        chunks: list[Chunk] = []
        current_section: list[str] = []
        current_lines: list[str] = []
        current_start_line = 1

        header_pattern = re.compile(r"^(#+)\s+(.*)$")

        for i, line in enumerate(lines, start=1):
            match = header_pattern.match(line)
            if match:
                if current_lines:
                    chunk_text = "".join(current_lines)
                    if chunk_text.strip():
                        chunks.append(Chunk(
                            text=chunk_text,
                            source_path=str(path),
                            start_line=current_start_line,
                            end_line=i - 1,
                            section_path=list(current_section),
                            metadata={"header_context": "/".join(current_section)},
                        ))

                level = len(match.group(1))
                title = match.group(2).strip()

                current_section = current_section[:level - 1] + [title]
                current_lines = [line]
                current_start_line = i
            else:
                current_lines.append(line)

        if current_lines:
            chunk_text = "".join(current_lines)
            if chunk_text.strip():
                chunks.append(Chunk(
                    text=chunk_text,
                    source_path=str(path),
                    start_line=current_start_line,
                    end_line=len(lines),
                    section_path=list(current_section),
                    metadata={"header_context": "/".join(current_section)},
                ))

        return chunks

    def _chunk_plaintext(self, path: Path) -> list[Chunk]:
        content = path.read_text(encoding="utf-8")
        lines = content.splitlines(keepends=True)
        return self._chunk_plaintext_lines(lines, str(path))

    def _chunk_plaintext_lines(self, lines: list[str], source_path: str) -> list[Chunk]:
        chunks: list[Chunk] = []
        current_text = ""
        current_start_line = 1

        for i, line in enumerate(lines, start=1):
            temp = current_text + line
            if len(temp) > self.chunk_size and current_text:
                chunks.append(Chunk(
                    text=current_text,
                    source_path=source_path,
                    start_line=current_start_line,
                    end_line=i - 1,
                ))

                overlap_size = min(self.chunk_overlap, len(current_text))
                current_text = current_text[-overlap_size:] + line
                current_start_line = i - 1
            else:
                current_text = temp

        if current_text.strip():
            chunks.append(Chunk(
                text=current_text,
                source_path=source_path,
                start_line=current_start_line,
                end_line=len(lines),
            ))

        return chunks

    def _chunk_json(self, path: Path) -> list[Chunk]:
        import json

        content = path.read_text(encoding="utf-8")
        lines = content.splitlines(keepends=True)

        try:
            data = json.loads(content)
            return self._chunk_json_data(data, str(path), lines)
        except json.JSONDecodeError:
            return self._chunk_plaintext_lines(lines, str(path))

    def _chunk_json_data(
        self, data: Any, source_path: str, lines: list[str],
        parent_key: str = "", depth: int = 0
    ) -> list[Chunk]:
        import json

        chunks: list[Chunk] = []
        max_depth = 3

        if depth > max_depth:
            return chunks

        if isinstance(data, dict):
            for key, value in data.items():
                current_key = f"{parent_key}.{key}" if parent_key else key
                if isinstance(value, (dict, list)):
                    chunks.extend(self._chunk_json_data(value, source_path, lines, current_key, depth + 1))
                else:
                    chunk_text = f"{current_key}: {json.dumps(value, ensure_ascii=False)}"
                    if len(chunk_text) <= self.chunk_size:
                        chunks.append(Chunk(
                            text=chunk_text,
                            source_path=source_path,
                            start_line=0,
                            end_line=0,
                            metadata={"json_key": current_key},
                        ))
        elif isinstance(data, list):
            for i, item in enumerate(data):
                current_key = f"{parent_key}[{i}]"
                if isinstance(item, (dict, list)):
                    chunks.extend(self._chunk_json_data(item, source_path, lines, current_key, depth + 1))
                else:
                    chunk_text = f"{current_key}: {json.dumps(item, ensure_ascii=False)}"
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
