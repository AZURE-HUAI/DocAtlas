import sys

from .runtime import DatasetNotChosen

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
