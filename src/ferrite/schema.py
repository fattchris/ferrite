"""Neo4j schema initialization (constraints and indexes)."""

import logging

logger = logging.getLogger(__name__)


SCHEMA_DDL: list[str] = [
    # Uniqueness constraints
    "CREATE CONSTRAINT fact_id_unique IF NOT EXISTS FOR (f:Fact) REQUIRE f.id IS UNIQUE",
    "CREATE CONSTRAINT entity_id_unique IF NOT EXISTS FOR (e:Entity) REQUIRE e.id IS UNIQUE",
    "CREATE CONSTRAINT alias_norm_unique IF NOT EXISTS FOR (a:Alias) REQUIRE a.norm IS UNIQUE",
    "CREATE CONSTRAINT episode_id_unique IF NOT EXISTS FOR (ep:Episode) REQUIRE ep.id IS UNIQUE",
    "CREATE CONSTRAINT observation_id_unique IF NOT EXISTS "
    "FOR (o:Observation) REQUIRE o.id IS UNIQUE",

    # Indexes for Fact lookups
    "CREATE INDEX fact_predicate_idx IF NOT EXISTS FOR (f:Fact) ON (f.predicate)",
    "CREATE INDEX fact_namespace_idx IF NOT EXISTS FOR (f:Fact) ON (f.namespace)",
    "CREATE INDEX fact_epistemic_idx IF NOT EXISTS FOR (f:Fact) ON (f.epistemic_state)",
    "CREATE INDEX fact_valid_at_idx IF NOT EXISTS FOR (f:Fact) ON (f.valid_at)",
    "CREATE INDEX fact_recorded_at_idx IF NOT EXISTS FOR (f:Fact) ON (f.recorded_at)",

    # Entity indexes
    "CREATE INDEX entity_type_idx IF NOT EXISTS FOR (e:Entity) ON (e.type)",
    "CREATE INDEX entity_name_idx IF NOT EXISTS FOR (e:Entity) ON (e.name)",

    # Episode index
    "CREATE INDEX episode_recorded_at_idx IF NOT EXISTS FOR (ep:Episode) ON (ep.recorded_at)",
]


FULLTEXT_DDL: list[str] = [
    "CREATE FULLTEXT INDEX fact_statement_fulltext IF NOT EXISTS "
    "FOR (f:Fact) ON EACH [f.statement]",
]


def init_schema(driver) -> None:
    """Initialize Neo4j schema: constraints, indexes, and fulltext index.
    Idempotent — safe to call multiple times.
    """
    with driver.session() as session:
        for ddl in SCHEMA_DDL:
            try:
                session.run(ddl).consume()
                logger.info(f"Executed: {ddl}")
            except Exception as e:
                logger.error(f"Failed executing {ddl}: {e}")
                raise

        for ddl in FULLTEXT_DDL:
            try:
                session.run(ddl).consume()
                logger.info(f"Executed: {ddl}")
            except Exception as e:
                logger.error(f"Failed executing {ddl}: {e}")
                raise

    logger.info("Schema initialization complete.")
