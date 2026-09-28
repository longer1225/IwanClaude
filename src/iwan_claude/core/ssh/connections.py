"""
SSH 连接库（M4a）— ~/.iwan/ssh/connections.json 的增删改查

【学习要点】
1. 整层克隆 ScheduleStore 的成熟配方：路径可注入（单测指 tmp_path）、
   坏文件改名让路（隔离而不是修复——一个逗号不该让 SSH 功能陪葬）、
   每次变更全量原子写（连接表天然小列表，增量化不值回票价）。
2. 校验只拦"接不上/接错"的硬伤（host 空白、port 越界、重名），不做
   网络探测：存连接 ≠ 连得上，连通性是终端/工具打开时的事——过早的
   "保存时测试连接"会把一个慢主机关死在编辑框里。
3. user 必填非空是刻意收紧：留空时 ssh 会静默用本地用户名去登录远端，
   十有八九连到错账号还看不出来。显式 > 智能。
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from iwan_claude.core.config import resolve_sessions_root

log = logging.getLogger(__name__)


# 连接字段校验；返回错误文案（None = 合法）。update 场景下字段可能缺省，None 跳过
def validate_conn(
    name: str | None, host: str | None, user: str | None, port: int | None
) -> str | None:
    if name is not None and not name.strip():
        return "名称不能为空"
    if host is not None:
        h = host.strip()
        if not h:
            return "主机地址不能为空"
        if any(c.isspace() for c in h):
            return "主机地址不能含空白字符"
    if user is not None and not user.strip():
        return "登录用户不能为空"
    if port is not None and (not isinstance(port, int) or not (1 <= port <= 65535)):
        return "端口应为 1..65535 的整数"
    return None


class SshConnStore:
    """
    SSH 连接表持久化（~/.iwan/ssh/connections.json）

    【学习要点】与 ScheduleStore 同构的三点决策（原子写/损坏隔离/浅拷贝
    出口）不再重复；本类独有的是 name 唯一约束——GUI 列表按名字点选，
    重名等于把"我连的是哪台"变成猜谜。
    """

    def __init__(self, path: Path | None = None) -> None:
        # 路径可注入：单测指向 tmp_path，生产落 ~/.iwan/ssh/connections.json
        self._path = (
            path
            if path is not None
            else resolve_sessions_root().parent / "ssh" / "connections.json"
        )
        self._conns: list[dict[str, Any]] = []
        self._loaded = False

    # 从磁盘载入连接表（幂等；坏文件改名保留 .corrupt-时间戳）
    def load(self) -> None:
        self._loaded = True
        if not self._path.exists():
            self._conns = []
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            stamp = datetime.now().strftime("%Y%m%d%H%M%S")
            bad = self._path.with_name(f"connections.corrupt-{stamp}.json")
            log.exception("ssh: 连接表损坏，改名保留后以空表启动：%s", self._path)
            try:
                os.replace(self._path, bad)
            except OSError:
                pass
            self._conns = []
            return
        rows = data.get("connections") if isinstance(data, dict) else None
        self._conns = [
            c for c in (rows or [])
            if isinstance(c, dict) and c.get("id") and c.get("host")
        ]

    # 全量原子写盘（tmp + replace）
    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps({"connections": self._conns}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, self._path)

    # 全部连接行（浅拷贝，按名称排序稳定展示）
    def list(self) -> list[dict[str, Any]]:
        if not self._loaded:
            self.load()
        return sorted((dict(c) for c in self._conns), key=lambda c: str(c.get("name", "")))

    # 按 id 找连接行；不存在返回 None
    def get(self, conn_id: str) -> dict[str, Any] | None:
        if not self._loaded:
            self.load()
        for c in self._conns:
            if c.get("id") == conn_id:
                return dict(c)
        return None

    # 新建连接：id 自动生成；字段非法或重名抛 ValueError
    def add(
        self, name: str, host: str, user: str, port: int, key_file: str = ""
    ) -> dict[str, Any]:
        if not self._loaded:
            self.load()
        err = validate_conn(name, host, user, port)
        if err is not None:
            raise ValueError(err)
        name = name.strip()
        if any(c.get("name") == name for c in self._conns):
            raise ValueError(f"名称已存在：{name}")
        row = {
            "id": uuid.uuid4().hex[:12],
            "name": name,
            "host": host.strip(),
            "user": user.strip(),
            "port": int(port),
            "key_file": key_file.strip(),
        }
        self._conns.append(row)
        self._save()
        return dict(row)

    # 局部更新连接（None=不动）；校验+查重（排除自身）；未知 id 返回 None
    def update(self, conn_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
        if not self._loaded:
            self.load()
        for c in self._conns:
            if c.get("id") != conn_id:
                continue
            merged = {
                "name": fields.get("name", c["name"]),
                "host": fields.get("host", c["host"]),
                "user": fields.get("user", c["user"]),
                "port": fields.get("port", c["port"]),
            }
            err = validate_conn(merged["name"], merged["host"], merged["user"], merged["port"])
            if err is not None:
                raise ValueError(err)
            new_name = str(merged["name"]).strip()
            if any(o.get("id") != conn_id and o.get("name") == new_name for o in self._conns):
                raise ValueError(f"名称已存在：{new_name}")
            for k in ("name", "host", "user", "port", "key_file"):
                if fields.get(k) is not None:
                    c[k] = str(fields[k]).strip() if k != "port" else int(fields[k])
            self._save()
            return dict(c)
        return None

    # 删除连接；返回是否真删掉（幂等语义由 handler 翻译成 ok=False 文案）
    def delete(self, conn_id: str) -> bool:
        if not self._loaded:
            self.load()
        before = len(self._conns)
        self._conns = [c for c in self._conns if c.get("id") != conn_id]
        if len(self._conns) != before:
            self._save()
            return True
        return False
