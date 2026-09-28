"""
SSH 连接表存储单测（M4a）

【学习要点】
1. 全程注入 tmp_path：绝不碰真实 ~/.iwan/ssh——生产路径的正确性由
   resolve_sessions_root 的单测负责，这里只测存储类自身语义。
2. 重名校验是"排除自身"的难例：update 把名字改成自己当前的名字必须放行，
   改成别人的必须拒绝——两个方向都要有断言，只测一个方向等于没测。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from iwan_claude.core.ssh.connections import SshConnStore


@pytest.fixture()
def store(tmp_path: Path) -> SshConnStore:
    # 功能：提供指向临时目录的空白连接表
    # 设计：路径注入是 SshConnStore 与生产唯一的差异点，fixture 收口它，
    # 每个测试从"空表"起步，避免测试间靠调用顺序传状态
    return SshConnStore(path=tmp_path / "connections.json")


# 功能：add 后 list 能读到完整行，且新 store 从磁盘重载结果一致
# 设计：同一 tmp 路径造第二个 store 实例走真实 load 分支——
# 只测内存态会漏掉"写盘格式与读盘解析不对称"这类最阴的持久化 bug
def test_add_persist_roundtrip(store: SshConnStore) -> None:
    row = store.add("home-server", "10.0.0.5", "iwan", 22)
    assert row["id"] and row["port"] == 22
    assert store.list()[0]["name"] == "home-server"
    again = SshConnStore(path=store._path)
    assert again.list() == store.list()


# 功能：非法字段（空名/空 host/host 含空白/空 user/端口越界）全部拒绝
# 设计：逐条 assert_raises 匹配文案前缀，确认每个校验分支各自生效——
# 合成一个"随便给个脏数据"的测试只会撞到第一个分支就通过
def test_add_validation(store: SshConnStore) -> None:
    with pytest.raises(ValueError, match="名称"):
        store.add("  ", "h", "u", 22)
    with pytest.raises(ValueError, match="主机地址不能为空"):
        store.add("n", " ", "u", 22)
    with pytest.raises(ValueError, match="空白"):
        store.add("n", "a b.example.com", "u", 22)
    with pytest.raises(ValueError, match="用户"):
        store.add("n", "h", "", 22)
    with pytest.raises(ValueError, match="端口"):
        store.add("n", "h", "u", 70000)


# 功能：重名 add 拒绝；同名删除后可以复用
# 设计：复用那一步验证的是"查重基于当前表而非历史累计"——
# 用已删除的 id 占坑做永久黑名单是这类存储最常见的过度防御
def test_dup_name(store: SshConnStore) -> None:
    row = store.add("dup", "h1", "u", 22)
    with pytest.raises(ValueError, match="名称已存在"):
        store.add("dup", "h2", "u", 22)
    assert store.delete(row["id"])
    assert store.add("dup", "h2", "u", 22)["host"] == "h2"


# 功能：update 局部改（None 不动）、改自己同名放行、改成他人名拒绝、未知 id 返回 None
# 设计：四个分支各打一次，其中"改回自己的名字"是排除自身逻辑的反例——
# 若查重没排除自身，这一步会假性失败，最容易在重构时悄悄回归
def test_update_semantics(store: SshConnStore) -> None:
    a = store.add("a", "ha", "ua", 22)
    store.add("b", "hb", "ub", 2222)
    out = store.update(a["id"], {"name": "a", "user": "root"})
    assert out is not None and out["name"] == "a" and out["user"] == "root"
    assert out["host"] == "ha" and out["port"] == 22
    with pytest.raises(ValueError, match="名称已存在"):
        store.update(a["id"], {"name": "b"})
    assert store.update("nope", {"host": "x"}) is None


# 功能：delete 命中返回 True 并落盘，二次删除返回 False 且不误写
# 设计：幂等语义在 handler 层翻译成"连接不存在"文案，这里只锁 store 的
# bool 契约；断言文件里只剩另一条，防止"删了内存忘了磁盘"
def test_delete(store: SshConnStore) -> None:
    a = store.add("a", "ha", "u", 22)
    store.add("b", "hb", "u", 22)
    assert store.delete(a["id"]) is True
    assert store.delete(a["id"]) is False
    data = json.loads(store._path.read_text(encoding="utf-8"))
    assert [c["name"] for c in data["connections"]] == ["b"]


# 功能：坏 JSON 文件被改名隔离后以空表启动，合法行字段缺失被静默剔除
# 设计：隔离（quarantine）而非修复是 Store 层的一致策略——测试同时盯住
# ".corrupt- 备份还在"与"功能没陪葬"两个不变式；坏行用缺 host 模拟，
# 走的是与 JSON 解析失败不同的第二道过滤
def test_corrupt_quarantine(tmp_path: Path) -> None:
    p = tmp_path / "connections.json"
    p.write_text("{ not json", encoding="utf-8")
    s = SshConnStore(path=p)
    assert s.list() == []
    assert any(f.name.startswith("connections.corrupt-") for f in tmp_path.iterdir())
    p.write_text(
        json.dumps({"connections": [{"id": "x", "host": "h", "name": "n", "user": "u", "port": 22}, {"id": "y"}]}),
        encoding="utf-8",
    )
    s2 = SshConnStore(path=p)
    assert [c["id"] for c in s2.list()] == ["x"]


# 功能：list 按名称排序且返回浅拷贝（改返回值不污染内部表）
# 设计：排序稳定是 GUI 列表不跳序的前提；浅拷贝测试直接改返回值再看
# 二次 list——若哪天有人"优化"成返回内部引用，这里立刻红
def test_list_sorted_and_defensive(store: SshConnStore) -> None:
    store.add("zeta", "h", "u", 22)
    store.add("alpha", "h", "u", 22)
    names = [c["name"] for c in store.list()]
    assert names == ["alpha", "zeta"]
    got = store.list()
    got[0]["name"] = "mutated"
    assert store.list()[0]["name"] == "alpha"
