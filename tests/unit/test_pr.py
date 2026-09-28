# 功能：验证 PR 评审取数链路（diff 请求头、超长截断、失败中文文案）与轮询差分纯函数
# 设计：不碰真实 git/GitHub——collect_context 猴补丁成固定坐标，httpx 换成注入
# MockTransport 的 AsyncClient 子类（test_http_ssrf.py 同款先例），网络层零出口；
# pr_new_numbers 是纯函数，直接用状态表断言"判新+登记"语义，含 seed 防回灌场景。
from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from iwan_claude.core import pr as core_pr
from iwan_claude.core.pr import REVIEW_DIFF_KEEP, fetch_pr_for_review, pr_new_numbers

# 评审取数测试共用的假仓库坐标（collect_context 被替换成这个返回）
_FAKE_CTX: dict[str, Any] = {
    "ok": True, "owner": "octo", "repo": "demo", "branch": "feat",
    "default_branch": "main", "ahead": 0, "behind": 0, "has_remote": True, "error": "",
}


# 把 httpx.AsyncClient 换成注入 MockTransport 的子类，返回 (被拦截请求列表) 供断言
def _install_mock_transport(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
) -> list[httpx.Request]:
    seen: list[httpx.Request] = []
    transport = httpx.MockTransport(lambda request: (seen.append(request), handler(request))[1])
    real_cls = httpx.AsyncClient

    class _MockClient(real_cls):  # type: ignore[misc,valid-type]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _MockClient)
    return seen


# GitHub 双请求的路由桩：Accept 带 v3.diff 的返回补丁文本，否则返回 PR 元数据 JSON
def _github_handler(*, diff_text: str = "@@ h\n+x", status: int = 200) -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.headers.get("Accept", "").startswith("application/vnd.github.v3.diff"):
            if status >= 400:
                return httpx.Response(status, json={"message": "Not Found"})
            return httpx.Response(status, text=diff_text)
        if status >= 400:
            return httpx.Response(status, json={"message": "Not Found"})
        return httpx.Response(200, json={"title": "加评审功能", "body": "详细描述"})
    return handle


@pytest.fixture(autouse=True)
def _fake_ctx(monkeypatch: pytest.MonkeyPatch) -> None:
    # 功能：让取数编排跳过真实 git 子进程，坐标恒为 octo/demo
    # 设计：autouse 保证本文件每个用例（含失败路径）都在同一坐标系下跑，
    # collect_context 自身逻辑已由 gitpanel/pr_giturl 等文件覆盖，不重复测
    async def _ctx(cwd: str) -> dict[str, Any]:
        return dict(_FAKE_CTX)
    monkeypatch.setattr(core_pr, "collect_context", _ctx)


# 功能：fetch_pr_for_review 的 diff 请求必须带 v3.diff Accept 头，meta 请求走 JSON；两路都成功时字段齐全
# 设计：用请求拦截列表验证"同一个 PR 号发了恰好两种 Accept 的请求"——这是对
# "diff 不走 _req 的 JSON 假设"这一设计决策的直接锁测试；改回 JSON 拼装会立刻红
async def test_fetch_sends_diff_accept_header(monkeypatch: pytest.MonkeyPatch) -> None:
    requests = _install_mock_transport(monkeypatch, _github_handler())
    data = await fetch_pr_for_review("/x", 7, "https://api.github.com", "tok")
    assert data["ok"] is True
    assert data["title"] == "加评审功能"
    assert data["body"] == "详细描述"
    assert data["diff"] == "@@ h\n+x"
    assert data["error"] == ""
    accepts = [r.headers.get("Accept", "") for r in requests]
    assert any(a.startswith("application/vnd.github.v3.diff") for a in accepts)
    assert any(a.startswith("application/vnd.github+json") for a in accepts)
    assert len(requests) == 2


# 功能：超长 diff 被截断到 REVIEW_DIFF_KEEP 并附"已截断"明示后缀
# 设计：造 2 倍上限的文本，断言"前缀内容保留 + 长度收缩 + 截断提示在场"三点——
# 只断长度会漏掉"模型必须知道自己只看了局部"这一产品要求
async def test_fetch_truncates_long_diff(monkeypatch: pytest.MonkeyPatch) -> None:
    big = "x" * (REVIEW_DIFF_KEEP * 2)
    _install_mock_transport(monkeypatch, _github_handler(diff_text=big))
    data = await fetch_pr_for_review("/x", 7, "https://api.github.com", "tok")
    assert data["ok"] is True
    assert data["diff"].startswith("x" * 100)
    assert "已截断" in data["diff"]
    assert len(data["diff"]) < REVIEW_DIFF_KEEP * 1.1


# 功能：PR 不存在（404）时返回 ok=False 与中文指引；无 token 的 404 要提示私有仓库需令牌
# 设计：错误文案是产品面（GUI 原样渲染），所以断"含中文关键词"而非全文匹配——
# 允许措辞微调不碎测试，但"拉取 PR #7 失败"与令牌指引两个语义点必须锁死
async def test_fetch_404_chinese_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_mock_transport(monkeypatch, _github_handler(status=404))
    data = await fetch_pr_for_review("/x", 7, "https://api.github.com", "")
    assert data["ok"] is False
    assert "拉取 PR #7" in data["error"]
    assert "私有仓库需要配置 GitHub 令牌" in data["error"]


# 功能：pr_new_numbers 只返回未登记的号并保持列表序，同批重复号只出一次
# 设计：seen 被就地修改是"判新+登记"一体的刻意契约——用调用前后快照断言，
# 若哪天有人把它改成纯函数（不登记），第二个断言会抓住调用方的重复评审回归
def test_pr_new_numbers_registers_in_place() -> None:
    rows = [{"number": 3}, {"number": 1}, {"number": 3}, {"number": 2}]
    seen: set[int] = {1}
    out = pr_new_numbers(rows, seen)
    assert out == [3, 2]
    assert seen == {1, 2, 3}
    # 再跑同一批：全部已登记，零新号（防重复烧 LLM 的核心不变式）
    assert pr_new_numbers(rows, seen) == []


# 功能：轮询首轮"seed 囤积"后，只有新出现的 PR 号会被送去评审
# 设计：模拟两轮真实轮询节奏（第一轮全量 5,6 只登记，第二轮 6,7,8 出 7,8），
# 并混入脏行（str/None number）验证容错——脏行若抛异常会炸掉整个轮询器
def test_pr_new_numbers_seed_then_follow_new() -> None:
    seen: set[int] = set()
    # 第一轮：daemon 上线即囤，返回值本身已是"新号"，轮询器靠 seeded 标志忽略它
    first = pr_new_numbers([{"number": 5}, {"number": 6}], seen)
    assert sorted(first) == [5, 6]
    assert seen == {5, 6}
    # 第二轮：脏数据不炸、老号跳过、真正的历史新号才浮出
    out = pr_new_numbers(
        [{"number": "bad"}, {"number": None}, {"number": 6}, {"number": 7}], seen,
    )
    assert out == [7]
    assert seen == {5, 6, 7}
