import sys

from .runtime import DatasetNotChosen

# `doctor` reports on the whole machine and must answer when nothing is set up
# yet — including the "no library chosen" state that stops the import below.
# So it is dispatched ahead of it rather than registered as a normal command.
if __name__ == "__main__" and sys.argv[1:2] == ["doctor"]:
    from .doctor import run

    raise SystemExit(run(as_json="--json" in sys.argv[2:]))

try:
    from .cli import main
except DatasetNotChosen as error:
    # The CLI serves one dataset per process and the shortcut names in `config`
    # all derive from it, so "no dataset chosen yet" surfaces during import.
    # Without this catch the user gets a traceback for what is really just a
    # missing dataset selection.
    print(error, file=sys.stderr)
    raise SystemExit(2) from None

# This guard is required: without it, importing this module would immediately run
# the CLI. Under `python -m docatlas` __name__ is "__main__", so it still works.
if __name__ == "__main__":
    raise SystemExit(main())
