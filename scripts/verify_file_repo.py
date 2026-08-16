"""One-shot verification: file repository integrity + fact provenance chain."""
import os
from neo4j import GraphDatabase

d = GraphDatabase.driver(
    "bolt://localhost:7687",
    auth=("neo4j", "53d009e2e90d1cee486c73125a3417c2"),
)
BASE = os.path.join(os.path.dirname(__file__), "..", "data", "files")

with d.session() as s:
    # 1. Every episode's source_file exists on disk
    r = s.run("MATCH (e:Episode) RETURN e.source_file AS f")
    files = [rec["f"] for rec in r]
    missing = [f for f in files if not os.path.exists(os.path.join(BASE, f))]
    print(f"episodes: {len(files)}, missing files: {len(missing)}")
    if missing:
        print("MISSING:", missing[:10])

    # 2. Orphan files on disk (not referenced by any episode)
    on_disk = set()
    for root, _dirs, fs in os.walk(os.path.join(BASE, "text")):
        rel = os.path.relpath(root, BASE)
        for fn in fs:
            on_disk.add(os.path.join(rel, fn))
    referenced = set(files)
    orphans = on_disk - referenced
    print(f"files on disk: {len(on_disk)}, orphans: {len(orphans)}")

    # 3. Facts must trace: Fact -SOURCED_FROM-> Episode -> real file
    r = s.run(
        """
        MATCH (f:Fact)-[:SOURCED_FROM]->(e:Episode)
        RETURN f.id AS fid, f.statement AS st, e.source_file AS file
        """
    )
    rows = list(r)
    broken = [x for x in rows if not os.path.exists(os.path.join(BASE, x["file"]))]
    print(f"facts with SOURCED_FROM: {len(rows)}, broken chains: {len(broken)}")

    # 4. Facts with NO episode link (orphan facts = untraceable)
    r = s.run(
        "MATCH (f:Fact) WHERE NOT (f)-[:SOURCED_FROM]->(:Episode) RETURN count(f) AS n"
    )
    print(f"facts WITHOUT episode link: {r.single()['n']}")

    # 5. _index.json consistency
    idx_path = os.path.join(BASE, "_index.json")
    if os.path.exists(idx_path):
        import json

        idx = json.load(open(idx_path))
        print(f"_index.json entries: {len(idx)}")
    else:
        print("_index.json: MISSING")

    # 6. Spot-check: does file content actually match what was ingested?
    import random

    sample = random.sample(rows, min(3, len(rows)))
    for rec in sample:
        p = os.path.join(BASE, rec["file"])
        content = open(p, errors="replace").read()
        print(f"spot {rec['fid'][:8]} | {len(content)} chars | {rec['st'][:60]}")
