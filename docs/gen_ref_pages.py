from pathlib import Path

import mkdocs_gen_files

nav = mkdocs_gen_files.Nav()

src = Path("log_panel")

for path in sorted(src.rglob("*.py")):
    # Skip migrations and management commands
    if "migrations" in path.parts or "management" in path.parts:
        continue

    module_path: Path = path.with_suffix(suffix="")
    doc_path: Path = path.with_suffix(suffix=".md")
    full_doc_path = Path("reference", doc_path)

    parts: tuple[str, ...] = tuple(module_path.parts)

    if parts[-1] == "__init__":
        parts: tuple[str, ...] = parts[:-1]
        doc_path: Path = doc_path.with_name(name="index.md")
        full_doc_path: Path = full_doc_path.with_name(name="index.md")

    if not parts:
        continue

    nav[parts] = doc_path.as_posix()

    with mkdocs_gen_files.open(full_doc_path, "w") as fd:
        ident = ".".join(parts)
        fd.write(f"::: {ident}\n")

    mkdocs_gen_files.set_edit_path(full_doc_path, path.as_posix())

with mkdocs_gen_files.open("reference/SUMMARY.md", "w") as nav_file:
    nav_file.writelines(nav.build_literate_nav())
