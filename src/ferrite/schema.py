"""Neo4j schema initialization (constraints and indexes).""""

from neo4j import Driver

from ferrite.config import get_settings


DDL_STATEMENTS = [
    # Uniqueness constraints
    "CREATE CONSTRAINT fact_id_unique IF NOT EXISTS FOR (f:Fact) REQUIRE f.id IS UNIQUE",
    "CREATE CONSTRAINT entity_id_unique IF NOT EXISTS FOR (e:Entity) REQUIRE e.id IS UNIQUE",
    "CREATE CONSTRAINT alias_norm_unique IF NOT EXISTS FOR (a:Alias) REQUIRE a.norm IS UNIQUE",
    "CREATE CONSTRAINT episode_id_unique IF NOT EXISTS FOR (e:Episode) REQUIRE e.id IS UNIQUE",
    "CREATE CONSTRAINT observation_id_unique IF NOT EXISTS FOR (o:Observation) REQUIRE o.id IS UNIQUE",
    # Lookup indexes
    "CREATE INDEX fact_predicate_idx IF NOT EXISTS FOR (f:Fact) ON (f.predicate)",
    "CREATE INDEX fact_namespace_idx IF NOT EXISTS FOR (f:Fact) ON (f.namespace)",
    "CREATE INDEX fact_epistemic_idx IF NOT EXISTS FOR (f:Fact) ON (f.epistemic_state)",
    "CREATE INDEX fact_valid_at_idx IF NOT EXISTS FOR (f:Fact) ON (f.valid_at)",
    "CREATE INDEX fact_recorded_at_idx IF NOT EXISTS FOR (f:Fact) ON (f.recorded_at)",
    "CREATE INDEX entity_name_idx IF NOT EXISTS FOR (e:Entity) ON (e.name)",
    # Full-text index for BM25 search
    "CREATE FULLTEXT INDEX fact_statement_fulltext IF NOT EXISTS FOR (f:Fact) ON EACH [f.statement]",
]


def init_schema(driver: Driver) -> None:
    """Execute schema initialization DDL.""""
    settings = get_settings()
    # Note: Neo4j vector indexes require specific version support.
    # We will use the full-text index for search in this MVP.
    with driver.session() as session:
        for stmt in DDL_STATEMENTS:
            session.run(stmt).consume()
        # Attempt to create vector index if supported (Neo4j 5.x+)
        try:
            session.run("""
                CREATE VECTOR INDEX fact_embedding_idx IF NOT EXISTS
                FOR (f:Fact) ON (f.embedding)
                OPTIONS {
                    indexConfig: {
                        `vector.dimensions`: $dims,
                        `vector.similarity_function`: "cosine"
                    }
                }
            """, dims=settings.EMBEDDING_DIMENSIONS).consume()
        except Exception:
            pass
