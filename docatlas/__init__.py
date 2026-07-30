"""DocAtlas — a local knowledge base for technical documentation.

Three layers:

    core docatlas/*.py       generic machinery; knows no specific site or domain
    sources/<name>.py        knows one documentation site: listing and parsing
    knowledge/<name>.py      knows one technical domain's vocabulary (optional)

Dependency direction within the core: constants/text -> dataset -> runtime ->
config -> util -> net -> db -> discover/htmlmd -> chunking -> documents ->
store -> relations -> crawl/assets/ondemand -> search/context ->
export/reports/validate -> cli/mcpserver.

Imports stay lazy here on purpose: `import docatlas` must not read dataset TOML
or pull in source adapters. A broken dataset config therefore still leaves the
package importable, which is what lets `docatlas_list_datasets` report which
config is broken.
"""


def main() -> int:
    from .cli import main as _main

    return _main()


__all__ = ["main"]
