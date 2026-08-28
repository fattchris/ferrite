"""One-time dedupe of duplicate Fact nodes (same namespace + statement).

Keeps the oldest fact per (namespace, statement), repoints SUBJECT/OBJECT/
SOURCED_FROM/SUPPORTS edges to it, then deletes the duplicates.
Idempotent: re-running finds nothing to do.

NOTE: statement_hash backfill uses the RAW statement (not lowercased) to
match the ingestion-side hash exactly. Dedupe grouping uses a normalized
statement so near-identical dups (case/whitespace) still merge.
"""

import hashlib
import os

from neo4j import GraphDatabase

URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
AUTH = ("neo4j", os.environ.get("NEO4J_PASSWORD", ""))

REL_TYPES = ("SUBJECT", "OBJECT", "SOURCED_FROM", "SUPPORTS")


def main():
    driver = GraphDatabase.driver(URI, auth=AUTH)
    with driver.session() as s:
        # Group facts by normalized statement within namespace
        groups = s.run(
            """
            MATCH (f:Fact) WHERE f.epistemic_state <> 'contradicted'
            WITH f.namespace AS ns, toLower(trim(f.statement)) AS stmt,
                 collect(f) AS facts
            WHERE size(facts) > 1
            RETURN ns, stmt, facts
            """
        ).data()

        merged = 0
        for g in groups:
            facts = sorted(g["facts"], key=lambda f: f["recorded_at"])
            keep, dups = facts[0], facts[1:]
            for dup in dups:
                # Repoint outbound rels from dup -> keep, then delete dup.
                # APOC not required: per-type MATCH + SET end node.
                for rtype in REL_TYPES:
                    s.run(
                        f"""
                        MATCH (d:Fact {{id: $dup_id}})-[r:{rtype}]->(t)
                        WHERE t <> $keep_id
                        MERGE (d)-[:{rtype}]->(k {{id: $keep_id}})
                        DELETE r
                        """,
                        dup_id=dup["id"],
                        keep_id=keep["id"],
                    )
                s.run(
                    "MATCH (d:Fact {id: $dup_id}) DETACH DELETE d",
                    dup_id=dup["id"],
                )
                merged += 1

        print(f"merged {merged} duplicate facts into their originals")

        # Backfill statement_hash on all remaining facts (raw statement,
        # matching ingestion.py's hash exactly)
        rows = s.run(
            "MATCH (f:Fact) WHERE f.statement_hash IS NULL "
            "RETURN f.id AS id, f.namespace AS ns, f.statement AS stmt"
        ).data()
        for r in rows:
            h = hashlib.sha256(f"{r['ns']}|{r['stmt']}".encode()).hexdigest()
            s.run(
                "MATCH (f:Fact {id: $id}) SET f.statement_hash = $h",
                id=r["id"], h=h,
            )
        print(f"backfilled statement_hash on {len(rows)} facts")
    driver.close()


if __name__ == "__main__":
    main()
