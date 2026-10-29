"""C# members and cyclomatic complexity.

The decisions follow JetBrains' ReSharper Cyclomatic Complexity plugin, which
scores the control-flow graph as edges − nodes + 2. On a syntax tree that is
1 plus each branch point: `if`, loops, `catch`, a `when` filter or guard, `?:`,
each `case` but not `default`, each switch expression arm but not a `_`
discard, and `&&`, `||`, `??`, `?.`, and the `and` / `or` patterns. Like the
plugin, unary `!` and `not` are not decisions.

Methods, constructors, finalizers, operators, and property, indexer, and event
accessors with a body are entries. An explicit interface implementation is
named `IStorage.Load`. Lambdas and local functions stay inside the
enclosing member. Nested types become their own namespace (`Ns.Outer.Inner`).
"""

from crapper.languages.treesitter import (
    complexity,
    end_line,
    node_text,
    parse,
    start_line,
)
from crapper.model import Function

_TYPES = {
    "class_declaration",
    "struct_declaration",
    "record_declaration",
    "record_struct_declaration",
    "interface_declaration",
}
_PLAIN_DECISIONS = {
    "if_statement",
    "for_statement",
    "foreach_statement",
    "while_statement",
    "do_statement",
    "catch_clause",
    "catch_filter_clause",
    "when_clause",
    "conditional_expression",
    "conditional_access_expression",
    "and_pattern",
    "or_pattern",
}
_LOGIC = {"&&", "||", "??"}
_MEMBERS = {
    "method_declaration",
    "constructor_declaration",
    "destructor_declaration",
    "operator_declaration",
    "conversion_operator_declaration",
}
_PROPERTIES = {"property_declaration", "indexer_declaration", "event_declaration"}
_BODIES = {"block", "arrow_expression_clause"}


def _is_decision(node) -> bool:
    kind = node.type
    if kind in _PLAIN_DECISIONS:
        return True
    if kind == "binary_expression":
        return any(child.type in _LOGIC for child in node.children)
    if kind == "switch_section":
        return bool(node.children) and node.children[0].type == "case"
    if kind == "switch_expression_arm":
        first = node.named_children[0] if node.named_children else None
        return first is None or first.type != "discard"
    return False


def _skip(node) -> bool:
    return node.type in _TYPES


def _has_body(node) -> bool:
    return any(child.type in _BODIES for child in node.children)


def _text(data: bytes, node, field: str) -> str | None:
    child = node.child_by_field_name(field)
    return node_text(data, child) if child is not None else None


def _file_scoped(data: bytes, node) -> list[str]:
    """A file-scoped namespace is a sibling of the types it covers, not a parent."""

    root = node
    while root.parent is not None:
        root = root.parent
    for child in root.children:
        if child.type == "file_scoped_namespace_declaration":
            name = _text(data, child, "name")
            return [name] if name else []
    return []


def _qualify(data: bytes, node) -> str:
    """File-scoped namespace first, then enclosing namespaces and types."""

    chain: list[str] = []
    current = node.parent
    while current is not None:
        if current.type in _TYPES or current.type == "namespace_declaration":
            name = _text(data, current, "name")
            if name:
                chain.append(name)
        current = current.parent
    chain.reverse()
    return ".".join(_file_scoped(data, node) + chain)


def _explicit(data: bytes, node, name: str | None) -> str | None:
    """`IStorage.Load` for an explicit interface implementation, so it does not
    collide with a public `Load` on the same class."""

    if name is None:
        return None
    specifier = next((c for c in node.children if c.type == "explicit_interface_specifier"), None)
    if specifier is None:
        return name
    interface = node_text(data, specifier).rstrip(".").strip()
    return f"{interface}.{name}"


def _member_name(data: bytes, node) -> str | None:
    kind = node.type
    if kind == "destructor_declaration":
        name = _text(data, node, "name")
        return f"~{name}" if name else None
    if kind == "operator_declaration":
        operator = _text(data, node, "operator")
        return f"operator {operator}" if operator else None
    if kind == "conversion_operator_declaration":
        target = _text(data, node, "type")
        return f"operator {target}" if target else None
    return _explicit(data, node, _text(data, node, "name"))


def _property_name(data: bytes, node) -> str | None:
    if node.type == "indexer_declaration":
        return _explicit(data, node, "this[]")
    return _explicit(data, node, _text(data, node, "name"))


def _function(data: bytes, node, name: str, path: str) -> Function:
    return Function(
        name=name,
        namespace=_qualify(data, node),
        complexity=complexity(node, _is_decision, _skip),
        start_line=start_line(node),
        end_line=end_line(node),
        path=path,
        language="csharp",
        start_byte=node.start_byte,
        end_byte=node.end_byte,
    )


def _accessors(data: bytes, node, path: str) -> list[Function]:
    name = _property_name(data, node)
    if name is None:
        return []
    arrow = next((c for c in node.children if c.type == "arrow_expression_clause"), None)
    if arrow is not None:
        return [_function(data, node, f"{name}.get", path)]
    found: list[Function] = []
    accessor_list = next((c for c in node.children if c.type == "accessor_list"), None)
    if accessor_list is None:
        return found
    for accessor in accessor_list.children:
        if accessor.type != "accessor_declaration" or not _has_body(accessor):
            continue
        keyword = next(
            (c for c in accessor.children if c.type in {"get", "set", "init", "add", "remove"}),
            None,
        )
        if keyword is not None:
            found.append(_function(data, accessor, f"{name}.{keyword.type}", path))
    return found


def functions_in_source(
    source: str, path: str, project_root: str | None = None
) -> list[Function]:
    del project_root
    data, tree = parse(source, "csharp")
    found: list[Function] = []
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        if node.type in _MEMBERS:
            name = _member_name(data, node)
            if name and _has_body(node):
                found.append(_function(data, node, name, path))
            continue
        if node.type in _PROPERTIES:
            found.extend(_accessors(data, node, path))
            continue
        stack.extend(reversed(node.children))
    return found
