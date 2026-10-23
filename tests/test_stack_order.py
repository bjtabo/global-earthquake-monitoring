import re
from pathlib import Path

import yaml

MAIN = Path(__file__).resolve().parents[1] / "infra" / "main.yaml"


class _Loader(yaml.SafeLoader):
    pass


def _untagged(loader, _suffix, node):  # !GetAtt, !Ref, !Sub read as plain values
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    return loader.construct_mapping(node)


_Loader.add_multi_constructor("!", _untagged)


def _strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from _strings(value)


def stack_dependencies():
    resources = yaml.load(MAIN.read_text(), Loader=_Loader)["Resources"]
    stacks = {n for n, r in resources.items() if r["Type"] == "AWS::CloudFormation::Stack"}
    deps = {}
    for name in stacks:
        params = resources[name]["Properties"].get("Parameters", {})
        depends_on = resources[name].get("DependsOn", [])
        # X.Outputs.Y in any form (scalar, list, nested) and ${X...} inside !Sub
        named = {n for s in _strings(params) for n in [s.split(".")[0], *re.findall(r"\$\{(\w+)", s)]}
        named |= {depends_on} if isinstance(depends_on, str) else set(depends_on)
        deps[name] = named & stacks
    return deps


def upstream(deps, name):
    seen, todo = set(), list(deps[name])
    while todo:
        stack = todo.pop()
        if stack not in seen:
            seen.add(stack)
            todo.extend(deps[stack])
    return seen


def test_edge_does_not_wait_for_database():
    assert "Database" not in upstream(stack_dependencies(), "Edge")
