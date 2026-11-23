"""YAML, read with the YAML 1.2 core schema and written without anchors.

PyYAML follows YAML 1.1, where ``no`` is false and ``0777`` is octal. The
loader here swaps in the 1.2 core rules: only true/false are booleans, dates
stay text and ``<<`` is an ordinary key. PyYAML is imported inside the
functions.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

from jzr_tools.core.dataconv import EMPTY, decode
from jzr_tools.core.dataconv.model import TOO_DEEP, YAML, ConvertError, Notes, Pairs

_BASE = "tag:yaml.org,2002:"
_ALLOWED = frozenset(_BASE + name for name in ("str", "int", "float", "bool", "null", "seq", "map"))
_REPLACED = frozenset(
    _BASE + name for name in ("bool", "int", "float", "null", "timestamp", "merge", "value")
)
_BOOL = re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$")
_NULL = re.compile(r"^(?:~|null|Null|NULL|)$")
_INT = re.compile(r"^(?:[-+]?[0-9]+|0o[0-7]+|0x[0-9a-fA-F]+)$")
_FLOAT = re.compile(
    r"^(?:[-+]?(?:\.[0-9]+|[0-9]+(?:\.[0-9]*)?)(?:[eE][-+]?[0-9]+)?"
    r"|[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN))$"
)

_classes: tuple[type, type] | None = None


def _core_resolvers(cls: type) -> None:
    cls.add_implicit_resolver(_BASE + "bool", _BOOL, list("tTfF"))
    cls.add_implicit_resolver(_BASE + "null", _NULL, ["~", "n", "N", ""])
    cls.add_implicit_resolver(_BASE + "int", _INT, list("-+0123456789"))
    cls.add_implicit_resolver(_BASE + "float", _FLOAT, list("-+0123456789."))


def _yaml_classes() -> tuple[type, type]:
    """The loader and dumper classes, built on first use."""
    global _classes
    if _classes is not None:
        return _classes
    import yaml

    class Loader(yaml.SafeLoader):
        pass

    Loader.yaml_implicit_resolvers = {
        first: [(tag, pattern) for tag, pattern in resolvers if tag not in _REPLACED]
        for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
    }
    _core_resolvers(Loader)

    def construct_int(loader: Any, node: Any) -> int:
        text = loader.construct_scalar(node)
        if text.startswith("0o"):
            return int(text[2:], 8)
        if text.startswith("0x"):
            return int(text[2:], 16)
        return int(text, 10)

    def construct_float(loader: Any, node: Any) -> float:
        text = loader.construct_scalar(node)
        lowered = text.lower()
        if lowered in (".inf", "+.inf"):
            return float("inf")
        if lowered == "-.inf":
            return float("-inf")
        if lowered == ".nan":
            return float("nan")
        return float(text)

    def construct_map(loader: Any, node: Any) -> Any:
        pairs = Pairs()
        yield pairs
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=False)
            if isinstance(key, (list, Pairs)):
                raise ConvertError(
                    f"invalid YAML at line {key_node.start_mark.line + 1} column "
                    f"{key_node.start_mark.column + 1}: a key must be text, a number, "
                    "true/false or null"
                )
            pairs.append((key, loader.construct_object(value_node, deep=False)))

    Loader.add_constructor(_BASE + "int", construct_int)
    Loader.add_constructor(_BASE + "float", construct_float)
    Loader.add_constructor(_BASE + "map", construct_map)

    class Dumper(yaml.SafeDumper):
        def ignore_aliases(self, data: Any) -> bool:
            return True

    # The 1.2 patterns too, so strings such as 0o17 or 1e3 get quoted.
    _core_resolvers(Dumper)

    def represent_time(dumper: Any, value: dt.time) -> Any:
        return dumper.represent_str(value.isoformat())

    Dumper.add_representer(dt.time, represent_time)
    _classes = (Loader, Dumper)
    return _classes


def _mark(error: Any) -> str:
    mark = getattr(error, "problem_mark", None) or getattr(error, "context_mark", None)
    problem = getattr(error, "problem", None) or str(error)
    if mark is None:
        return f"invalid YAML: {problem}"
    return f"invalid YAML at line {mark.line + 1} column {mark.column + 1}: {problem}"


def _tag_name(tag: str) -> str:
    return "!!" + tag[len(_BASE):] if tag.startswith(_BASE) else tag


def _check_tags(root: Any) -> None:
    import yaml

    seen: set[int] = set()
    stack = [root]
    while stack:
        node = stack.pop()
        if id(node) in seen:
            continue
        seen.add(id(node))
        if node.tag not in _ALLOWED:
            raise ConvertError(f"YAML tag {_tag_name(node.tag)} is not supported")
        if isinstance(node, yaml.MappingNode):
            for key, value in node.value:
                stack.extend((key, value))
        elif isinstance(node, yaml.SequenceNode):
            stack.extend(node.value)


def read_yaml(raw: bytes, options: object, notes: Notes, target: str) -> Any:
    import yaml

    text = decode(raw)
    loader_class, _ = _yaml_classes()
    try:
        documents = list(yaml.compose_all(text, Loader=loader_class))
        if len(documents) > 1:
            raise ConvertError(
                f"use one YAML document; this input has {len(documents)} "
                "(split it, or remove the extra --- lines)"
            )
        if not documents or documents[0] is None:
            raise ConvertError(EMPTY)
        _check_tags(documents[0])
        loader = loader_class("")
        try:
            value = loader.construct_document(documents[0])
        finally:
            loader.dispose()
    except yaml.YAMLError as error:
        raise ConvertError(_mark(error)) from None
    except RecursionError:
        raise ConvertError(TOO_DEEP) from None
    except ValueError as error:  # a tagged scalar that does not parse, such as !!int x
        raise ConvertError(f"invalid YAML: {error}") from None
    notes.add(YAML)
    return value


def write_yaml(value: Any, options: object, notes: Notes) -> tuple[bytes, str]:
    import yaml

    _, dumper_class = _yaml_classes()
    text = yaml.dump(
        value,
        Dumper=dumper_class,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=1 << 30,
    )
    if text.endswith("\n...\n"):  # a lone scalar
        text = text[: -len("...\n")]
    return text.encode("utf-8"), ""
