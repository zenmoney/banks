"""Validate the full YAML sequence and snapshot prompts before any agent starts."""
from pathlib import Path
import sys

import yaml


TASKS = {"update-existing", "add-missing", "rework-rejected", "rework-changes", "merge-approved"}


class UniqueLoader(yaml.SafeLoader):
    pass


def unique_mapping(loader, node):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if not isinstance(key, str):
            raise ValueError("YAML mapping keys must be strings")
        if key in result:
            raise ValueError(f"duplicate YAML key: {key}")
        result[key] = loader.construct_object(value_node)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)


def text(value, location):
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise ValueError(f"{location} must be a nonempty string without NUL characters")
    return value


def load_steps(config):
    config = Path(config).absolute()
    document = yaml.load(config.read_text(encoding="utf-8"), Loader=UniqueLoader)
    if not isinstance(document, dict) or document.keys() != {"steps"}:
        raise ValueError("YAML must contain exactly one top-level 'steps' list")
    rows = document["steps"]
    if not isinstance(rows, list) or not rows:
        raise ValueError("steps must be a nonempty list")
    steps = []
    for index, row in enumerate(rows, 1):
        location = f"steps[{index}]"
        if not isinstance(row, dict) or not row or row.keys() - {"task", "prompt", "prompt_file"}:
            raise ValueError(f"{location} requires task, prompt or prompt_file; unknown keys are not allowed")
        if "prompt" in row and "prompt_file" in row:
            raise ValueError(f"{location}: prompt and prompt_file are mutually exclusive")
        task = None
        if "task" in row:
            task = text(row["task"], f"{location}.task")
            if task not in TASKS:
                raise ValueError(f"{location}: unknown task {task!r}")
        prompt = None
        if "prompt" in row:
            prompt = text(row["prompt"], f"{location}.prompt")
        elif "prompt_file" in row:
            path = config.parent / text(row["prompt_file"], f"{location}.prompt_file")
            try:
                prompt = text(path.read_text(encoding="utf-8"), f"{location}.prompt_file ({path})")
            except (OSError, UnicodeError) as exc:
                raise ValueError(f"{location}.prompt_file ({path}): {exc}") from exc
        steps.append({"task": task, "prompt": prompt})
    return steps


def prepare(config, output):
    steps = load_steps(config)
    output = Path(output)
    for index, step in enumerate(steps, 1):
        directory = output / str(index)
        directory.mkdir()
        (directory / "task").write_text(step["task"] or "", encoding="utf-8")
        (directory / "label").write_text(f"{index}-{step['task'] or 'prompt'}", encoding="utf-8")
        if step["prompt"] is not None:
            (directory / "prompt.md").write_text(step["prompt"], encoding="utf-8")
    if any(step["task"] for step in steps):
        (output / "needs-catalog").touch()
    (output / "order").write_text("".join(f"{index}\n" for index in range(1, len(steps) + 1)))


if __name__ == "__main__":
    try:
        prepare(sys.argv[1], sys.argv[2])
    except (OSError, UnicodeError, ValueError, yaml.YAMLError) as exc:
        print(f"Invalid step configuration: {exc}", file=sys.stderr)
        sys.exit(2)
