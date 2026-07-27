"""MCP 服务器入口——从任何工作目录启动都能用。

DocAtlas 不是装进 Python 环境的包，就是一个普通文件夹，所以 `python -m docatlas`
只有在仓库根目录下才找得到它。MCP 客户端各自决定用什么工作目录启动子进程，也不
都支持 `cwd` 字段：Claude Code 的 stdio 配置就只有 `command` / `args` / `env`
三项（`claude mcp add` 也没有 `--cwd`），照着写 `cwd` 会静默失效，报错停在
`No module named docatlas`，看不出是配置问题（BUG-021）。

用这个文件当入口就绕开了整件事：Python 启动一个脚本时，会把**脚本所在的目录**
放进 `sys.path[0]`，也就是这里的仓库根。于是 `import docatlas` 从任何目录都成立，
配置里只要给出这个文件的绝对路径：

    {"command": "python", "args": ["C:/你的路径/DocAtlas/mcp_server.py"]}

不需要 cwd，不需要 PYTHONPATH，也不需要 pip install。
"""

from docatlas.mcpserver import serve

if __name__ == "__main__":
    raise SystemExit(serve())
