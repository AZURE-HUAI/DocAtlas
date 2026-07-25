from .cli import main

# 必须有这层守卫：没有它，任何人 import 到这个模块都会当场跑起命令行来。
# `python -m docatlas` 时 __name__ 就是 "__main__"，照常工作。
if __name__ == "__main__":
    raise SystemExit(main())
