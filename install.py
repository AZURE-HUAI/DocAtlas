"""DocAtlas 一键安装：选数据位置、装技能、注册 MCP，装完自己验一遍。

    python install.py                      # 全部装上，数据放 <仓库>/data
    python install.py --data-dir D:/Docs   # 数据放别的盘（程序和数据可以分开）
    python install.py --print              # 只打印 MCP 配置片段，不动任何文件
    python install.py --skip-skill         # 只注册 MCP
    python install.py --skip-mcp           # 只装技能

用 Python 写而不是写成 .ps1/.sh：DocAtlas 本来就要 Python 才能跑，用它当安装器
就自动跨平台，也不用为每个系统各维护一份、各自跑偏。

两条纪律：

1. **先验证，再写配置。** 注册之前先从一个陌生目录把 MCP 服务器真起一次、走完
   握手，通不过就什么都不写——一次失败留下半截配置，比没装还难查。
2. **不代改别人的现成配置。** Claude Code 交给官方的 `claude mcp add`；Codex 只在
   没有该条目时**追加**；其余客户端只打印片段。几千行的配置经一次解析重写会丢
   保真度，不值得为省一次粘贴去冒这个险。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
LAUNCHER = REPO_ROOT / "mcp_server.py"
SERVER_NAME = "docatlas"
SKILL_NAME = "docatlas"
MIN_PYTHON = (3, 11)

# 技能装到 <家目录>/<客户端>/skills/<技能名>/。加新客户端就在这里加一行。
SKILL_CLIENTS = {
    "claude-code": Path.home() / ".claude",
    "codex": Path.home() / ".codex",
}


class Failed(Exception):
    """安装失败，带一句人话。"""


# --------------------------------------------------------------------------
# 数据位置
# --------------------------------------------------------------------------


def save_choices(data_dir: str | None, dataset: str | None) -> tuple[Path, str | None]:
    """把"数据放哪"和"默认查哪个库"写进 `.docatlas-local.toml`。

    必须落到文件里：MCP 客户端起子进程时不会带上你终端里的环境变量，只靠
    `DOCATLAS_HOME` / `DOCATLAS_DATASET` 的话，命令行查得到的库、MCP 查不到。

    没传的那一项保持原样，不会被这次安装抹掉。
    """
    from docatlas.runtime import LOCAL_SETTINGS, available_dataset_ids, local_settings

    settings = local_settings()
    default_dir = REPO_ROOT / "data"
    if data_dir:
        target = Path(data_dir).expanduser().resolve()
        settings["home"] = str(target)
    else:
        target = Path(settings.get("home") or default_dir).resolve()
    target.mkdir(parents=True, exist_ok=True)
    if target == default_dir.resolve():
        settings.pop("home", None)

    if dataset:
        if dataset not in (available := available_dataset_ids()):
            raise Failed(f"没有这个数据集：{dataset}。可选：{'、'.join(available)}")
        settings["dataset"] = dataset

    if settings:
        LOCAL_SETTINGS.write_text(
            "# 由 install.py 生成，一机一份，不入库。\n"
            + "".join(f"{key} = {json.dumps(str(value))}\n"
                      for key, value in sorted(settings.items())),
            encoding="utf-8",
        )
    else:
        LOCAL_SETTINGS.unlink(missing_ok=True)
    return target, settings.get("dataset")


# --------------------------------------------------------------------------
# MCP
# --------------------------------------------------------------------------


def mcp_config(python: str) -> dict:
    """给所有客户端的同一份 stdio 配置。

    **不写 `cwd`。** DocAtlas 不是装进 Python 环境的包，就是个普通文件夹，
    `python -m docatlas mcp` 只有在仓库目录下才找得到它；而 `cwd` 是各家客户端
    自己决定支不支持的字段——Codex 支持，Claude Code 的 stdio 配置就只有
    `command` / `args` / `env`，写了会静默失效，只留下一句 `No module named
    docatlas`（BUG-021）。走 `mcp_server.py` 则不依赖任何一方：Python 会把脚本
    所在目录放进 `sys.path`，从任何工作目录启动都成立。

    `command` 用绝对路径：客户端起子进程时的 PATH 不一定和你的终端一样。
    """
    return {"command": python, "args": [str(LAUNCHER)]}


def verify_handshake(python: str) -> str:
    """从陌生目录、不带 PYTHONPATH 起一次真实进程，走完 initialize。

    退出码为 0 说明不了服务器活着——必须看它回了什么。
    """
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONIOENCODING"] = "utf-8"
    request = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                   "clientInfo": {"name": "install", "version": "1"}},
    })
    proc = subprocess.run(
        [python, str(LAUNCHER)],
        input=request + "\n", cwd=Path.home(), env=env,
        capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    reply = (proc.stdout or "").strip().splitlines()
    if not reply:
        raise Failed(f"MCP 服务器没有回应 initialize。\n{proc.stderr.strip()[:600]}")
    name = json.loads(reply[0]).get("result", {}).get("serverInfo", {}).get("name")
    if not name:
        raise Failed(f"initialize 的回应里没有 serverInfo：{reply[0][:300]}")
    return name


def register_claude_code(python: str) -> str | None:
    claude = shutil.which("claude")
    if not claude:
        return None
    # 交给官方命令写，不去手改 ~/.claude.json——那个文件里还存着别的状态。
    subprocess.run([claude, "mcp", "remove", SERVER_NAME, "-s", "user"],
                   capture_output=True, text=True)
    done = subprocess.run(
        [claude, "mcp", "add", SERVER_NAME, "-s", "user", "--", python, str(LAUNCHER)],
        capture_output=True, text=True,
    )
    if done.returncode != 0:
        raise Failed(f"claude mcp add 失败：{done.stderr.strip()[:300]}")
    return "claude-code"


def register_codex(python: str) -> str | None:
    """Codex 用 TOML。只在没有该条目时追加，已有就让用户自己改。

    没有第三方 TOML 写库，重写整份配置会丢注释和格式；而已经存在同名段落时再追加
    一段，会直接让 Codex 解析失败——那是把用户的配置搞坏，不是安装。
    """
    config = Path.home() / ".codex" / "config.toml"
    if not config.parent.exists():
        return None
    text = config.read_text(encoding="utf-8") if config.exists() else ""
    section = f"[mcp_servers.{SERVER_NAME}]"
    block = (
        f"\n{section}\n"
        f"command = {json.dumps(python)}\n"
        f"args = [{json.dumps(str(LAUNCHER))}]\n"
    )
    if section in text:
        print(f"  codex：已存在 {section}，没有改动。要换成新写法就手工替换为：")
        print("".join(f"    {line}\n" for line in block.strip().splitlines()))
        return None
    with config.open("a", encoding="utf-8") as handle:
        handle.write(block)
    return "codex"


# --------------------------------------------------------------------------
# 技能
# --------------------------------------------------------------------------


def render_skill() -> dict[str, str]:
    """把 skills/docatlas/*.md 的占位符按当前数据集填好。

    填充由 `docatlas.cli` 做：只有它认识数据集。这里只管把文件放对地方。
    """
    from docatlas.cli import skill_substitutions

    source = REPO_ROOT / "skills" / SKILL_NAME
    if not (source / "SKILL.md").exists():
        raise Failed(f"找不到技能原本：{source / 'SKILL.md'}")
    substitutions = skill_substitutions()
    rendered = {}
    for path in sorted(source.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        for name, value in substitutions.items():
            text = text.replace("{{" + name + "}}", value)
        if leftover := sorted(set(re.findall(r"\{\{([A-Z_]+)\}\}", text))):
            raise Failed(f"{path.name} 里有没人认识的占位符：{'、'.join(leftover)}")
        rendered[path.name] = text
    return rendered


def install_skill(rendered: dict[str, str]) -> list[str]:
    done = []
    for client, home in SKILL_CLIENTS.items():
        # 只装到真的存在的客户端：凭空造一个目录只会留下没人读的文件。
        if not home.exists():
            continue
        target = home / "skills" / SKILL_NAME
        target.mkdir(parents=True, exist_ok=True)
        for name, text in rendered.items():
            # 一律 UTF-8 无 BOM：Codex 读到 BOM 就认不出 SKILL.md 的 frontmatter。
            (target / name).write_bytes(text.encode("utf-8"))
        verify_skill(target, rendered)
        done.append(f"{client} → {target}")
    return done


def verify_skill(target: Path, rendered: dict[str, str]) -> None:
    """装完自己验一遍。"命令跑完没报错"不等于客户端认得出这个技能。"""
    for name in rendered:
        raw = (target / name).read_bytes()
        if len(raw) < 3:
            raise Failed(f"{target / name} 写出来是空的")
        if raw[:3] == b"\xef\xbb\xbf":
            raise Failed(f"{target / name} 带 BOM，Codex 会认不出来")
    if not (target / "SKILL.md").read_text(encoding="utf-8").startswith("---"):
        raise Failed("SKILL.md 开头不是 frontmatter，客户端不会把它当技能")


# --------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="安装 DocAtlas 的技能与 MCP 服务器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--data-dir", help="数据放哪（默认 <仓库>/data）")
    parser.add_argument("--dataset", help="默认查哪个库（不指定就每次自己说）")
    parser.add_argument("--print", dest="print_only", action="store_true",
                        help="只打印 MCP 配置片段，不动任何文件")
    parser.add_argument("--skip-mcp", action="store_true", help="不注册 MCP")
    parser.add_argument("--skip-skill", action="store_true", help="不装技能")
    args = parser.parse_args()

    if sys.version_info < MIN_PYTHON:
        version = ".".join(map(str, MIN_PYTHON))
        print(f"需要 Python {version}+，当前是 {sys.version.split()[0]}", file=sys.stderr)
        return 1
    if not LAUNCHER.exists():
        print(f"找不到 MCP 入口：{LAUNCHER}", file=sys.stderr)
        return 1

    # 用**跑这个安装器的**解释器，而不是去 PATH 上碰运气：能跑通安装的那一个，
    # 就是能跑通 DocAtlas 的那一个。
    python = sys.executable
    snippet = json.dumps({"mcpServers": {SERVER_NAME: mcp_config(python)}}, indent=2)
    if args.print_only:
        print(snippet)
        return 0

    try:
        data_dir, dataset = save_choices(args.data_dir, args.dataset)
        print(f"程序：{REPO_ROOT}")
        print(f"数据：{data_dir}")
        print(f"默认库：{dataset or '未指定（每次自己说，或 --dataset 定下来）'}")
        print(f"Python：{python}\n")

        if not args.skip_mcp:
            print(f"✓ MCP 握手通过：{verify_handshake(python)}"
                  f"（从 {Path.home()} 启动，未设 PYTHONPATH）")
            registered = [name for name in
                          (register_claude_code(python), register_codex(python)) if name]
            if registered:
                print(f"✓ 已注册 MCP：{'、'.join(registered)}（客户端重启后生效）")
            else:
                print("  没有可自动注册的客户端。")
            print("\n其他客户端（Claude Desktop / Cursor / Cline…）把这段填进配置：")
            print(snippet)

        if not args.skip_skill:
            installed = install_skill(render_skill())
            if installed:
                print("\n✓ 已装技能：")
                for line in installed:
                    print(f"    {line}")
            else:
                print(f"\n  没有检测到支持技能的客户端"
                      f"（找过：{'、'.join(SKILL_CLIENTS)}）。")
    except Failed as error:
        print(f"\n安装失败：{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
