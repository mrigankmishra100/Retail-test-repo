"""Install the reviewed shared tracing hook into a local Deployment Studio checkout.

No credentials, network calls, cloud writes, or automatic service restarts.
Preserves backups and refuses unfamiliar source instead of guessing a patch.
"""
import argparse
import ast
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]


def prepare(root):
    studio = root.resolve() / "backend" / "studio"
    edits = {}
    for name, import_line, before, after in (
        ("retail_workflow.py", "from .retail_langfuse import shared_tracing_snapshot",
         "**generated_secrets(), **database_config", "**generated_secrets(), **database_config, **shared_tracing_snapshot()"),
        ("retail_recipe.py", "from .retail_langfuse import with_shared_tracing",
         "    return values", "    return with_shared_tracing(values, private)"),
    ):
        path = studio / name
        if path.is_symlink():
            raise ValueError("Dashboard source must not be a symlink")
        source = path.read_text(encoding="utf-8")
        if import_line in source and after in source:
            continue
        if import_line in source or source.count(before) != 1 or after in source:
            raise ValueError("Dashboard source differs from the reviewed integration; no files changed")
        tree = ast.parse(source)
        first = tree.body[0]
        if not isinstance(first, ast.Expr) or not isinstance(first.value, ast.Constant) or not isinstance(first.value.value, str):
            raise ValueError("Expected dashboard module documentation; no files changed")
        lines = source.splitlines(keepends=True)
        lines.insert(first.end_lineno, import_line + "\n")
        replacement = "".join(lines).replace(before, after, 1)
        ast.parse(replacement)
        backup = path.with_name(path.name + ".before-shared-langfuse")
        if backup.exists() and backup.read_bytes() != path.read_bytes():
            raise ValueError("An older dashboard integration backup exists; review before updating")
        edits[path] = replacement
    helper = studio / "retail_langfuse.py"
    expected = (ROOT / "deployment/dashboard/retail_langfuse.py").read_bytes()
    if helper.exists() and helper.read_bytes() != expected:
        raise ValueError("Existing tracing integration differs; review before updating")
    ast.parse(expected)
    return edits, helper, expected


def install(root, apply=False):
    edits, helper, expected = prepare(root)
    if apply:
        # Validate every input before mutating any source file.
        for path in edits:
            backup = path.with_name(path.name + ".before-shared-langfuse")
            if not backup.exists():
                shutil.copy2(path, backup)
        if not helper.exists():
            helper.write_bytes(expected)
        for path, source in edits.items():
            temporary = path.with_name(path.name + ".shared-langfuse.tmp")
            temporary.write_text(source, encoding="utf-8")
            temporary.replace(path)
    return len(edits)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dashboard-root", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    count = install(args.dashboard_root, args.apply)
    print("Shared Langfuse hook " + ("installed" if args.apply else "validated") + f"; {count} dashboard files require patching.")


if __name__ == "__main__":
    main()
