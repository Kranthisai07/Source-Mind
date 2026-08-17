"""Both writers of a `derives` relation must agree on edge direction.

Two independent code paths create derives relations:

  * services/memory/relations.py — ingestion-time detection
  * services/conflict/resolver.py — the merged conflict resolution

The canonical convention is fixed by RelationType.DERIVES:

    derives — source was logically derived from target

so source_memory_id is the DERIVED memory and target_memory_id is its
origin. relations.py follows it: _classify_relation is called as
(existing, new), the prompt defines derives as "B is a logical inference
from A" where B is the new memory, and the write sets
source_memory_id=memory.id (B, derived) and target_memory_id=cand_id
(A, origin).

resolver.py originally wrote the reverse, which would have made the graph
inconsistent for any traversal of derives edges. These tests pin the
convention so neither path can drift from it again.
"""

from __future__ import annotations

import ast
import pathlib
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

API_ROOT = pathlib.Path(__file__).resolve().parents[2]
PACKAGE = API_ROOT / "sourcemind"


@pytest.mark.unit
def test_model_documents_the_canonical_direction():
    """The convention must stay stated where both writers can find it."""
    from sourcemind.models.memory_relation import RelationType

    doc = RelationType.__doc__ or ""
    assert "derives" in doc
    assert "source was logically derived from target" in doc, (
        "the canonical direction is no longer documented on RelationType"
    )


@pytest.mark.unit
def test_ingestion_writes_derived_as_source_and_origin_as_target():
    """relations.py: source_memory_id = the new memory, target = the candidate.

    Verified structurally rather than by mocking, because the direction lives
    in the keyword arguments of the MemoryRelation construction.
    """
    source = (PACKAGE / "services" / "memory" / "relations.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)

    constructions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "MemoryRelation"
    ]
    assert constructions, "no MemoryRelation construction found in relations.py"

    kwargs = {
        kw.arg: ast.unparse(kw.value)
        for kw in constructions[0].keywords
        if kw.arg
    }
    # `memory` is the newly ingested memory (the derived one);
    # `cand_id` is the pre-existing candidate it was inferred from.
    assert kwargs.get("source_memory_id") == "memory.id", (
        f"expected the new memory as source, got {kwargs.get('source_memory_id')!r}"
    )
    assert kwargs.get("target_memory_id") == "cand_id", (
        f"expected the candidate as target, got {kwargs.get('target_memory_id')!r}"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_merge_writes_the_same_direction_as_ingestion():
    """resolver.py must write derived-as-source, matching relations.py.

    Logically equivalent inputs: in both paths one memory is derived from
    another. Ingestion derives the NEW memory from an EXISTING one; a merge
    derives the MERGED memory from each ORIGINAL. The derived memory must be
    source_memory_id in both.
    """
    from sourcemind.models.memory import Memory
    from sourcemind.services.conflict.resolver import resolve_conflict

    mem_a_id, mem_b_id = str(uuid.uuid4()), str(uuid.uuid4())
    ws_id = str(uuid.UUID("00000000-0000-4000-8000-000000000010"))
    statements: list[tuple[str, dict]] = []
    added: list = []

    async def execute_side_effect(stmt, params=None, **kwargs):
        s = str(stmt)
        statements.append((s, params or {}))
        r = MagicMock()
        if "SELECT memory_a_id" in s:
            r.fetchone = MagicMock(return_value=(mem_a_id, mem_b_id, ws_id))
        else:
            r.fetchone = MagicMock(return_value=None)
            r.first = MagicMock(return_value=None)
        return r

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=execute_side_effect)
    session.add = MagicMock(side_effect=added.append)

    # Give the merged Memory a stable id at flush, so the direction is checkable.
    merged_id = uuid.uuid4()

    async def fake_flush():
        for obj in added:
            if isinstance(obj, Memory) and obj.id is None:
                obj.id = merged_id

    session.flush = AsyncMock(side_effect=fake_flush)

    await resolve_conflict(
        session=session,
        conflict_id=uuid.uuid4(),
        resolver_id=uuid.uuid4(),
        resolution_type="merged",
        merged_content="Unified statement.",
        openai_client=None,
    )

    relations = [
        (s, p) for s, p in statements if "INSERT INTO memory_relations" in s
    ]
    assert len(relations) == 2, "expected one derives edge per original memory"

    for stmt, params in relations:
        # Column order in the INSERT is (source_memory_id, target_memory_id),
        # and the bound names must line up with it.
        assert stmt.index("source_memory_id") < stmt.index("target_memory_id")
        assert stmt.index(":derived") < stmt.index(":origin"), (
            "the derived memory must be bound to source_memory_id"
        )
        assert params["derived"] == str(merged_id), (
            "the merged memory is the derived one and belongs in source_memory_id"
        )
        assert params["origin"] in {mem_a_id, mem_b_id}, (
            "each original memory is an origin and belongs in target_memory_id"
        )

    assert {p["origin"] for _, p in relations} == {mem_a_id, mem_b_id}


@pytest.mark.unit
def test_only_the_two_known_paths_decide_a_derives_direction():
    """A third direction-deciding writer must be reconciled deliberately.

    Scoped to modules that write a literal 'derives' relation, because those
    are the ones choosing an edge direction. Two other modules touch
    memory_relations without deciding one:

      * models/memory_relation.py defines the schema
      * services/attribution/versioning.py copies a memory's existing
        relations onto its new version, preserving whatever direction and
        type they already had

    Neither can introduce an inconsistency, so neither is included here.
    """
    deciders: set[str] = set()
    for path in PACKAGE.rglob("*.py"):
        # models/ declares the schema and the RelationType enum; the class
        # statement itself matches "MemoryRelation(" without writing a row.
        if "__pycache__" in str(path) or path.parent.name == "models":
            continue
        content = path.read_text(encoding="utf-8")
        writes_relation = (
            "INSERT INTO memory_relations" in content or "MemoryRelation(" in content
        )
        names_derives = '"derives"' in content or "'derives'" in content
        if writes_relation and names_derives:
            deciders.add(path.relative_to(PACKAGE).as_posix())

    assert deciders == {
        "services/memory/relations.py",
        "services/conflict/resolver.py",
    }, (
        "the set of modules deciding a derives direction changed. Any new one "
        "must follow 'source was logically derived from target'. "
        f"Found: {sorted(deciders)}"
    )


@pytest.mark.unit
def test_version_copy_preserves_direction_rather_than_choosing_one():
    """versioning.py must copy relations as-is, not rebuild them.

    If it ever started constructing relations with its own column ordering it
    would become a third direction-deciding writer.
    """
    source = (PACKAGE / "services" / "attribution" / "versioning.py").read_text(
        encoding="utf-8"
    )
    assert "MemoryRelation(" not in source, (
        "versioning.py now constructs relations directly; it must either copy "
        "them or follow the canonical derives direction explicitly"
    )
