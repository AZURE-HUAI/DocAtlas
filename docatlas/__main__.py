import sys

from .runtime import DatasetNotChosen

try:
    from .cli import main
except DatasetNotChosen as error:
    # 命令行一个进程只服务一个库，`config` 里那些快捷名字都是它的派生，所以
    # "还没定下查哪个库"这件事在 import 期间就会冒出来。不接住的话用户看到的
    # 是一段堆栈，而问题其实只是少说了一句查哪个库。
    print(error, file=sys.stderr)
    raise SystemExit(2) from None

# 必须有这层守卫：没有它，任何人 import 到这个模块都会当场跑起命令行来。
# `python -m docatlas` 时 __name__ 就是 "__main__"，照常工作。
if __name__ == "__main__":
    raise SystemExit(main())
