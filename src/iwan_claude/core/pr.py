"""
Pull Request 支撑模块（M2）— git 远端解析 + httpx 直连 api.github.com

【学习要点】
1. 为什么不用 gh CLI：本机未装、也不能假设用户装了它；git 子进程是本项目
   已验证的既有通路，REST API 只差一个令牌——最小公分母路径。
2. origin URL 三种形态都要认：scp 式 `git@host:owner/repo.git`、
   `https://host/owner/repo(.git)`、`ssh://[user@]host/owner/repo(.git)`。
   解析器不锁死 github.com 主机名：企业版 base_url 用户照用。
3. 一切失败都返回 {ok: False, error: 人类可读文案}，不向上抛：这些字符串会
   原样出现在 GUI 上——错误提示是产品面，不是崩溃现场。
4. push 与建 PR 都是改变共享状态的动作：本模块只"执行并如实汇报"，
   二次确认的责任在 GUI；审计靠 logger 记录 + 返回体 pushed 标记。
"""
from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from iwan_claude.core.gitpanel import run_git as _git

log = logging.getLogger(__name__)

# 从 origin URL 解析 GitHub 坐标 (owner, repo)；解析不出返回 None（非 GitHub 远端/畸形 URL）
def parse_github_remote(url: str) -> tuple[str, str] | None:
    u = url.strip()
    # 形态 A：scp 式 [user@]host:owner/repo（无 scheme 前缀）
    m = re.match(r"^[^@\s/]+@[^:\s/]+:(?P<rest>\S+)$", u)
    if m is None:
        # 形态 B：scheme://[user@]host/owner/repo
        m2 = re.match(r"^\w+://(?:[^@/\s]+@)?[^/]+/(?P<rest>\S+)$", u)
        if m2 is None:
            return None
        rest = m2.group("rest")
    else:
        rest = m.group("rest")
    parts = [p for p in rest.split("/") if p]
    if len(parts) < 2:
        return None
    owner, repo = parts[0], parts[1]
    if repo.endswith(".git"):
        repo = repo[:-4]
    if not owner or not repo:
        return None
    return owner, repo

# 汇总 pr.context 所需的全部本地信息（纯 git，无网络）：坐标/分支/领先落后/默认分支
async def collect_context(cwd: str) -> dict[str, Any]:
    ok, root, err = await _git(cwd, "rev-parse", "--show-toplevel")
    if not ok:
        return _ctx_err(err or "不是 git 仓库（或 git 不可用）")
    ok, branch, _ = await _git(cwd, "branch", "--show-current")
    cur = branch if ok else ""

    ok, url, _ = await _git(cwd, "remote", "get-url", "origin")
    if not ok or not url:
        return _ctx_err(
            "未配置 origin 远端——PR 功能需要先添加远程仓库", branch=cur, has_remote=False,
        )
    parsed = parse_github_remote(url)
    if parsed is None:
        return _ctx_err(f"origin URL 无法解析出 owner/repo：{url}", branch=cur)
    owner, repo = parsed

    # 默认分支：origin/HEAD 的符号引用；没设过就留空（pr.create 时向 GitHub 查仓库信息兜底）
    default_branch = ""
    ok, sym, _ = await _git(cwd, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if ok and sym.startswith("origin/"):
        default_branch = sym[len("origin/"):]

    ahead, behind = 0, 0
    if cur:
        ok, cnt, _ = await _git(cwd, "rev-list", "--count", "--left-right", f"origin/{cur}...HEAD")
        if ok and cnt:
            # left-right 输出 "behind ahead"（origin 独有数 / HEAD 独有数）
            pieces = cnt.split()
            if len(pieces) == 2:
                behind, ahead = int(pieces[0]), int(pieces[1])
        elif default_branch:
            # 本地分支还没推送过：相对默认分支数领先量
            ok, cnt2, _ = await _git(cwd, "rev-list", "--count", f"origin/{default_branch}..HEAD")
            if ok and cnt2.isdigit():
                ahead = int(cnt2)
    return {
        "ok": True,
        "owner": owner,
        "repo": repo,
        "branch": cur,
        "default_branch": default_branch,
        "ahead": ahead,
        "behind": behind,
        "has_remote": True,
        "error": "",
    }

# context 失败行的统一形状（字段齐全，GUI 无脑渲染）
def _ctx_err(msg: str, branch: str = "", has_remote: bool = True) -> dict[str, Any]:
    return {
        "ok": False, "owner": "", "repo": "", "branch": branch,
        "default_branch": "", "ahead": 0, "behind": 0,
        "has_remote": has_remote, "error": msg,
    }


class GitHubClient:
    """
    api.github.com 薄封装（REST，无第三方 SDK）

    【学习要点】
    - 令牌从配置注入（[github] token / IWAN_GITHUB_TOKEN），空令牌仍可匿名读
      公开仓库（60 次/小时限流），写操作直接拒绝并给出配置指引；
    - 一切失败归一为 (ok=False, data=None, error文案)，HTTP 状态与 GitHub 的
      message 字段拼进文案——限流(403/429)、缺权限(401/403)、不存在(404)各有针对性提示。
    """

    def __init__(self, base_url: str, token: str) -> None:
        self._base = base_url.rstrip("/")
        self._token = token

    # 组一次请求的公共头（UA 是 GitHub 硬性要求；有 token 才带 Authorization）
    def _headers(self) -> dict[str, str]:
        h = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "iwanclaude-gui",
        }
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    # 发一个 GitHub API 请求：返回 (ok, JSON数据|None, 错误摘要)
    async def _req(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> tuple[bool, Any, str]:
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(20.0, connect=10.0),
                limits=httpx.Limits(max_connections=10),
            ) as client:
                resp = await client.request(
                    method,
                    f"{self._base}{path}",
                    headers=self._headers(),
                    params=params,
                    json=json_body,
                )
        except httpx.HTTPError as e:
            return False, None, f"网络失败：{type(e).__name__}: {e}"
        if resp.status_code >= 400:
            return False, None, self._api_error(resp)
        try:
            return True, resp.json(), ""
        except ValueError:
            return False, None, f"响应不是 JSON（HTTP {resp.status_code}）"

    # 把 GitHub 错误响应翻成带指引的中文文案（状态码语义见 REST 文档）
    def _api_error(self, resp: httpx.Response) -> str:
        msg = ""
        try:
            msg = str(resp.json().get("message", ""))
        except ValueError:
            msg = resp.text[:200]
        code = resp.status_code
        if code in (401, 403) and not self._token:
            return (
                f"HTTP {code}：{msg or '需要身份认证'}"
                "——未配置 GitHub 令牌（config.toml [github] token 或 IWAN_GITHUB_TOKEN）"
            )
        if code == 404 and not self._token:
            return "HTTP 404：仓库不存在或未授权——私有仓库需要配置 GitHub 令牌才能看见"
        if code in (403, 429):
            return f"HTTP {code}：{msg or '触发 API 限流'}——稍后重试或配置令牌提高配额"
        return f"HTTP {code}：{msg}"

    # 拉 PR 列表并归一化成 GUI 行
    async def list_pulls(
        self, owner: str, repo: str, state: str, page: int,
    ) -> tuple[bool, list[dict[str, Any]], str]:
        ok, data, err = await self._req(
            "GET",
            f"/repos/{owner}/{repo}/pulls",
            params={"state": state if state in ("open", "closed", "all") else "open",
                    "per_page": 30, "page": max(1, page)},
        )
        if not ok or not isinstance(data, list):
            return False, [], err or "响应格式异常"
        rows = [
            {
                "number": int(p.get("number", 0)),
                "title": str(p.get("title", "")),
                "author": str((p.get("user") or {}).get("login", "")),
                "state": str(p.get("state", "")),
                "updated_at": str(p.get("updated_at", "")),
                "url": str(p.get("html_url", "")),
                "head_ref": str((p.get("head") or {}).get("ref", "")),
                "base_ref": str((p.get("base") or {}).get("ref", "")),
                "draft": bool(p.get("draft", False)),
            }
            for p in data
        ]
        return True, rows, ""

    # 查仓库默认分支（pr.create 没显式 base 且本地 origin/HEAD 未设时的兜底）
    async def default_branch(self, owner: str, repo: str) -> tuple[bool, str, str]:
        ok, data, err = await self._req("GET", f"/repos/{owner}/{repo}")
        if not ok or not isinstance(data, dict):
            return False, "", err or "响应格式异常"
        return True, str(data.get("default_branch", "")), ""

    # 建 PR：head/base 为 "分支名"（同仓库推送后 GitHub 自动识别 owner:repo 前缀）
    async def create_pull(
        self, owner: str, repo: str, title: str, body: str,
        base: str, head: str, draft: bool,
    ) -> tuple[bool, dict[str, Any], str]:
        if not self._token:
            return False, {}, (
                "创建 PR 需要 GitHub 令牌（config.toml [github] token 或 IWAN_GITHUB_TOKEN）"
            )
        ok, data, err = await self._req(
            "POST",
            f"/repos/{owner}/{repo}/pulls",
            json_body={"title": title, "body": body, "head": head, "base": base, "draft": draft},
        )
        if not ok or not isinstance(data, dict):
            return False, {}, err or "创建失败"
        return True, {
            "number": int(data.get("number", 0)),
            "url": str(data.get("html_url", "")),
        }, ""

# pr.list 端到端编排：本地解析坐标 → GitHub 拉列表；返回 PrListResult 形状
async def list_pulls_for_cwd(
    cwd: str, state: str, page: int, base_url: str, token: str,
) -> dict[str, Any]:
    ctx = await collect_context(cwd)
    if not ctx["ok"]:
        return {"ok": False, "error": ctx["error"], "pulls": []}
    gh = GitHubClient(base_url, token)
    ok, rows, err = await gh.list_pulls(ctx["owner"], ctx["repo"], state, page)
    return {"ok": ok, "error": err, "pulls": rows}

# pr.create 端到端编排：坐标/分支校验 →（有未推送提交才）push → POST 建 PR；成功/失败都进审计日志
async def create_pull_for_cwd(
    cwd: str, title: str, body: str, base: str, head: str,
    draft: bool, base_url: str, token: str,
) -> dict[str, Any]:
    def fail(msg: str) -> dict[str, Any]:
        log.warning("pr.create 失败（cwd=%s）：%s", cwd, msg)
        return {"ok": False, "number": 0, "url": "", "pushed": False, "error": msg}

    if not title.strip():
        return fail("PR 标题不能为空")
    if not token:
        return fail("创建 PR 需要 GitHub 令牌（config.toml [github] token 或 IWAN_GITHUB_TOKEN）")
    ctx = await collect_context(cwd)
    if not ctx["ok"]:
        return fail(ctx["error"])
    src = head.strip() or ctx["branch"]
    if not src:
        return fail("当前处于游离 HEAD——请先切换到具名分支再创建 PR")

    gh = GitHubClient(base_url, token)
    dst = base.strip()
    if not dst:
        dst = ctx["default_branch"]
        if not dst:
            ok_b, dst2, err_b = await gh.default_branch(ctx["owner"], ctx["repo"])
            if not ok_b or not dst2:
                return fail(f"无法确定目标分支：{err_b or '本地 origin/HEAD 未设置'}")
            dst = dst2

    pushed = False
    # 只在"确有未推送提交"或远端分支还不存在时 push——干净重发不打网络
    ok_ls, ls_out, _ = await _git(cwd, "ls-remote", "--heads", "origin", src)
    remote_exists = bool(ok_ls and ls_out.strip())
    if not remote_exists or ctx["ahead"] > 0:
        ok_p, _, err_p = await _git(cwd, "push", "-u", "origin", src, timeout_s=120.0)
        if not ok_p:
            return fail(f"推送分支 {src} 失败：{err_p}")
        pushed = True
        ctx["ahead"] = 0

    ok_c, data, err_c = await gh.create_pull(
        ctx["owner"], ctx["repo"], title.strip(), body, dst, src, draft,
    )
    if not ok_c:
        tail = "" if pushed else "（无需推送）"
        return fail(f"分支已推送{tail}，但 GitHub 拒绝了创建请求：{err_c}")
    log.info(
        "pr.create 成功：%s/%s #%s（pushed=%s）",
        ctx["owner"], ctx["repo"], data["number"], pushed,
    )
    return {"ok": True, "number": data["number"], "url": data["url"], "pushed": pushed, "error": ""}
