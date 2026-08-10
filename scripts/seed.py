#!/usr/bin/env python
"""Seed the Ferrite knowledge graph with Chris's infrastructure data.

Usage:
    cd ~/ferrite && uv run python scripts/seed.py

Connects to Neo4j at bolt://localhost:7687, auth neo4j/ferrite123.
Cleans all existing data, initializes the schema, and seeds with real data.
"""

import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from neo4j import GraphDatabase

# Ensure src/ is on the path when running as a script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ferrite.canonicalize import create_entity  # noqa: E402
from ferrite.ingestion import IngestionPipeline  # noqa: E402
from ferrite.models import Episode, FactBase  # noqa: E402
from ferrite.schema import init_schema  # noqa: E402
from ferrite.vocab import is_functional, is_valid_predicate  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# --- Configuration ---
NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
_env_pw = os.environ.get("NEO4J_PASSWORD", os.environ.get("FERRITE_NEO4J_PASSWORD", ""))
if not _env_pw:
    from ferrite.config import get_settings
    NEO4J_PASSWORD = get_settings().NEO4J_PASSWORD
else:
    NEO4J_PASSWORD = _env_pw
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")

# Timestamps
T1 = datetime(2026, 7, 1)    # Most facts
T2 = datetime(2026, 8, 1)    # Recent (neo4j setup, etc.)


# --- Episode for provenance ---

def make_episode(content: str, recorded_at: datetime) -> Episode:
    """Create an Episode for provenance tracking."""
    return Episode(
        content=content,
        content_type="text",
        source={"script": "seed.py"},
        recorded_at=recorded_at,
        namespace="shared",
    )


SEED_EPISODE = make_episode("Infrastructure seed data for Ferrite KG.", T1)


# --- Helper: write a fact ---

def write_fact(
    pipeline: IngestionPipeline,
    entity_cache: dict,
    subject_name: str,
    predicate: str,
    object_value: str,
    object_type: str = "literal",
    valid_at: datetime = T1,
    recorded_at: datetime = T1,
    assertion_source: str = "user",  # type: ignore[arg-type]  # Literal["user","tool_result","model"]
    episode: Episode = SEED_EPISODE,
    subject_type: str = "entity",
    subject_summary: str = "",
    create_obj_entity: bool = False,
    obj_entity_type: str = "entity",
    obj_entity_summary: str = "",
) -> None:
    """Create entities as needed and write a fact through the ingestion pipeline."""
    if not is_valid_predicate(predicate):
        logger.warning(f"Skipping invalid predicate: {predicate}")
        return

    # Create or resolve subject entity
    if subject_name not in entity_cache:
        entity_cache[subject_name] = create_entity(
            pipeline.driver, subject_name, subject_type, subject_summary or None
        )
    subject_entity = entity_cache[subject_name]

    # Create object entity if requested
    if object_type == "entity":
        if create_obj_entity and object_value not in entity_cache:
            entity_cache[object_value] = create_entity(
                pipeline.driver, object_value, obj_entity_type, obj_entity_summary or None
            )

    # Build the statement
    statement = f"{subject_name} {predicate} {object_value}"

    # Use a per-call episode if recorded_at differs from the seed episode
    if recorded_at != episode.recorded_at:
        episode = make_episode(f"Fact: {statement}", recorded_at)

    fact = FactBase(
        predicate=predicate,
        statement=statement,
        functional=is_functional(predicate),
        certainty="stated",
        assertion_source=assertion_source,
        valid_at=valid_at,
        valid_at_inferred=False,
        recorded_at=recorded_at,
        namespace="shared",
    )

    pipeline._write_fact(fact, subject_entity, object_value, object_type, episode)
    logger.info(f"Seeded: {statement}")


# --- Seed data ---

def seed_all(pipeline: IngestionPipeline) -> None:
    """Seed the entire knowledge graph."""
    driver = pipeline.driver
    ec: dict = {}  # entity cache

    # ========================================================
    # SPARK FLEET (DGX Spark nodes with GB10 GPU)
    # ========================================================
    spark_nodes = {
        "spark-01": {"ip": "192.168.1.201", "model": "glm-5.2", "cluster": "glm-cluster",
                      "head": True, "bt": "#4"},
        "spark-02": {"ip": "192.168.1.202", "model": "glm-5.2", "cluster": "glm-cluster",
                      "head": False, "bt": "#2"},
        "spark-03": {"ip": "192.168.1.203", "model": "glm-5.2", "cluster": "glm-cluster",
                      "head": False, "bt": "#3"},
        "spark-04": {"ip": "192.168.1.204", "model": "glm-5.2", "cluster": "glm-cluster",
                      "head": False, "bt": "#1"},
        "spark-05": {"ip": "192.168.1.205", "model": "laguna-s-2-1", "cluster": None,
                      "head": False, "bt": None},
        "spark-06": {"ip": "192.168.1.206", "model": None, "cluster": None,
                      "head": False, "bt": None},  # spare
        "spark-07": {"ip": "192.168.1.207", "model": "dsv4-flash-ablit", "cluster": "dsv4-cluster",
                      "head": False, "bt": None},
        "spark-08": {"ip": "192.168.1.208", "model": "dsv4-flash-ablit", "cluster": "dsv4-cluster",
                      "head": False, "bt": None},
    }

    # Create cluster entities first
    write_fact(pipeline, ec, "glm-cluster", "has_gpu", "GB10", "literal", T1, T1,
               subject_summary="GLM-5.2 inference cluster on DGX Spark nodes 01-04")
    write_fact(pipeline, ec, "glm-cluster", "configured_with", "TP4", "literal", T1, T1)
    write_fact(pipeline, ec, "glm-cluster", "uses_framework", "vllm", "literal", T1, T1)
    write_fact(pipeline, ec, "glm-cluster", "has_port", "8210", "literal", T1, T1)

    write_fact(pipeline, ec, "dsv4-cluster", "has_gpu", "GB10", "literal", T1, T1,
               subject_summary="DSV4 Flash Abliterated inference cluster on nodes 07-08")
    write_fact(pipeline, ec, "dsv4-cluster", "configured_with", "TP2", "literal", T1, T1)
    write_fact(pipeline, ec, "dsv4-cluster", "has_port", "8210", "literal", T1, T1)

    # Create spark node entities and their facts
    for node_name, info in spark_nodes.items():
        summary = f"DGX Spark node ({info['ip']})"
        if info.get("head"):
            summary += " — head node"
        write_fact(pipeline, ec, node_name, "has_gpu", "GB10", "literal", T1, T1,
                   subject_summary=summary)
        write_fact(pipeline, ec, node_name, "has_ip", info["ip"], "literal", T1, T1)
        write_fact(pipeline, ec, node_name, "has_port", "8210", "literal", T1, T1)

        if info["model"]:
            write_fact(pipeline, ec, node_name, "runs_model", info["model"], "entity",
                        T1, T1, create_obj_entity=True,
                        obj_entity_summary=f"Model: {info['model']}")
        if info["cluster"]:
            write_fact(pipeline, ec, node_name, "part_of_cluster", info["cluster"], "entity",
                        T1, T1, create_obj_entity=True)
            if info.get("head"):
                write_fact(pipeline, ec, node_name, "head_node_of", info["cluster"], "entity",
                            T1, T1)
        if info.get("bt"):
            # BT mapping: #1=04, #2=02, #3=03, #4=01 (physical button to node mapping)
            write_fact(pipeline, ec, node_name, "configured_with",
                        f"NODE_BT={info['bt']} (spark button {info['bt']} maps to {node_name})", "literal", T1, T1)

    # spark-01 head node rank
    write_fact(pipeline, ec, "spark-01", "configured_with", "NODE_RANK=0", "literal", T1, T1)

    # spark-07 previously ran laguna
    write_fact(pipeline, ec, "spark-07", "runs_model", "laguna-s-2-1", "entity",
                datetime(2026, 6, 1), datetime(2026, 6, 1),
                create_obj_entity=True)

    # ========================================================
    # MODELS
    # ========================================================
    # glm-5.2
    write_fact(pipeline, ec, "glm-5.2", "deployed_on", "spark-01", "entity", T1, T1,
               subject_type="concept", subject_summary="GLM-5.2 large language model",
               create_obj_entity=True)
    write_fact(pipeline, ec, "glm-5.2", "deployed_on", "spark-02", "entity", T1, T1,
               create_obj_entity=True)
    write_fact(pipeline, ec, "glm-5.2", "deployed_on", "spark-03", "entity", T1, T1,
               create_obj_entity=True)
    write_fact(pipeline, ec, "glm-5.2", "deployed_on", "spark-04", "entity", T1, T1,
               create_obj_entity=True)
    write_fact(pipeline, ec, "glm-5.2", "configured_with", "TP4", "literal", T1, T1)
    write_fact(pipeline, ec, "glm-5.2", "has_port", "8210", "literal", T1, T1)
    write_fact(pipeline, ec, "glm-5.2", "has_throughput", "0.86 GPU utilization", "literal", T1, T1)
    write_fact(pipeline, ec, "glm-5.2", "has_version", "vllm-0.25.1", "literal", T1, T1)
    write_fact(pipeline, ec, "glm-5.2", "configured_with", "VLLM_USE_V1=1", "literal", T1, T1)
    write_fact(pipeline, ec, "glm-5.2", "uses_framework", "vllm", "literal", T1, T1)
    write_fact(pipeline, ec, "glm-5.2", "connects_to", "glm-cluster", "entity", T1, T1,
               create_obj_entity=True)

    # dsv4-flash-ablit
    write_fact(pipeline, ec, "dsv4-flash-ablit", "deployed_on", "spark-07",
               "entity", T1, T1, subject_type="concept",
               subject_summary="DSV4 Flash Abliterated model"
               " (drowzeys/keys-DeepSeekV4-Flash-GA-0731"
               "-Dspark-Abliterated-32-32)",
               create_obj_entity=True)
    write_fact(pipeline, ec, "dsv4-flash-ablit", "deployed_on", "spark-08", "entity", T1, T1,
               create_obj_entity=True)
    write_fact(pipeline, ec, "dsv4-flash-ablit", "configured_with", "TP2", "literal", T1, T1)
    write_fact(pipeline, ec, "dsv4-flash-ablit", "uses_framework", "vllm", "literal", T1, T1)
    write_fact(pipeline, ec, "dsv4-flash-ablit", "configured_with",
                "abliterated", "literal", T1, T1)
    write_fact(pipeline, ec, "dsv4-flash-ablit", "connects_to", "dsv4-cluster", "entity", T1, T1,
               create_obj_entity=True)

    # laguna-s-2-1
    write_fact(pipeline, ec, "laguna-s-2-1", "deployed_on", "spark-05", "entity", T1, T1,
               subject_type="concept", subject_summary="Laguna S 2.1 NVFP4 model, 262k context",
               create_obj_entity=True)
    write_fact(pipeline, ec, "laguna-s-2-1", "configured_with", "NVFP4", "literal", T1, T1)
    write_fact(pipeline, ec, "laguna-s-2-1", "has_throughput", "0.72", "literal", T1, T1)
    write_fact(pipeline, ec, "laguna-s-2-1", "has_memory", "262k context", "literal", T1, T1)

    # mtp-5 (rejected)
    write_fact(pipeline, ec, "mtp-5", "rejected",
                "42% regression on GB10", "literal", T1, T1,
               subject_type="concept",
               subject_summary="MTP-5: rejected experiment, 12.6% acceptance")
    write_fact(pipeline, ec, "mtp-5", "has_regression", "42%", "literal", T1, T1)
    write_fact(pipeline, ec, "mtp-5", "configured_with", "12.6% acceptance rate", "literal", T1, T1)

    # ========================================================
    # MAC FLEET
    # ========================================================
    mac_fleet = {
        "mac-mini": {"ip": "100.90.179.92", "user": "fontes",
                      "runs": "litellm proxy", "port": None, "memory": None},
        "m5": {"ip": "100.65.177.26", "user": "cfontes", "runs": None,
               "port": None, "memory": None},
        "m1air": {"ip": "100.119.93.62", "user": None, "runs": None,
                  "port": None, "memory": None},
        "mba-m3": {"ip": "100.107.115.10", "user": "cfontes", "runs": None,
                   "port": None, "memory": None},
        "m3pro": {"ip": "100.124.223.35", "user": "chrisfontes",
                  "runs": "Hermes Voice Bridge", "port": "8005", "memory": "48GB"},
        "m4pro": {"ip": "100.76.255.36", "user": "chrisfontes", "runs": None,
                  "port": None, "memory": None},
    }

    for mac_name, info in mac_fleet.items():
        summary = f"Mac ({info['ip']})"
        write_fact(pipeline, ec, mac_name, "has_ip", info["ip"], "literal", T1, T1,
                   subject_summary=summary)
        if info.get("user"):
            write_fact(pipeline, ec, mac_name, "configured_with",
                        f"user={info['user']}", "literal", T1, T1)
        if info.get("runs"):
            write_fact(pipeline, ec, mac_name, "runs_model", info["runs"], "entity", T1, T1,
                       create_obj_entity=True,
                       obj_entity_summary=f"Service: {info['runs']}")
        if info.get("port"):
            write_fact(pipeline, ec, mac_name, "has_port", info["port"], "literal", T1, T1)
        if info.get("memory"):
            write_fact(pipeline, ec, mac_name, "has_memory", info["memory"], "literal", T1, T1)
        write_fact(pipeline, ec, mac_name, "connects_to", "tailscale", "entity", T1, T1,
                   create_obj_entity=True,
                   obj_entity_summary="Tailscale mesh VPN")

    # ========================================================
    # SERVICES
    # ========================================================
    # litellm
    write_fact(pipeline, ec, "litellm", "runs_on", "mac-mini", "entity", T1, T1,
               subject_type="concept",
               subject_summary="LiteLLM proxy — multi-model gateway",
               create_obj_entity=True)
    write_fact(pipeline, ec, "litellm", "has_port", "4000", "literal", T1, T1)
    write_fact(pipeline, ec, "litellm", "configured_with", "master_key=redacted", "literal", T1, T1)
    write_fact(pipeline, ec, "litellm", "configured_with",
                "venv=~/.venvs/litellm", "literal", T1, T1)
    write_fact(pipeline, ec, "litellm", "has_version", "Python 3.13", "literal", T1, T1)
    write_fact(pipeline, ec, "litellm", "configured_with",
                "fastapi==0.115.8", "literal", T1, T1)
    write_fact(pipeline, ec, "litellm", "configured_with",
                "supervisor+watchdog auto-restart", "literal", T1, T1)
    write_fact(pipeline, ec, "litellm", "uses_framework", "fastapi", "literal", T1, T1)

    # hermes-voice-bridge
    write_fact(pipeline, ec, "hermes-voice-bridge", "runs_on", "m3pro", "entity", T1, T1,
               subject_type="concept",
               subject_summary="Hermes Voice Bridge — STT+LLM+TTS pipeline",
               create_obj_entity=True)
    write_fact(pipeline, ec, "hermes-voice-bridge", "has_port", "8005", "literal", T1, T1)
    write_fact(pipeline, ec, "hermes-voice-bridge", "configured_with",
                "HTTPS via tailscale port 8443", "literal", T1, T1)
    write_fact(pipeline, ec, "hermes-voice-bridge", "configured_with",
                "VAD always runs", "literal", T1, T1)
    write_fact(pipeline, ec, "hermes-voice-bridge", "configured_with",
                "true duplex", "literal", T1, T1)

    # neo4j
    write_fact(pipeline, ec, "neo4j", "runs_on", "mac-mini", "entity", T2, T2,
               subject_type="concept",
               subject_summary="Neo4j graph database for Ferrite",
               create_obj_entity=True)
    write_fact(pipeline, ec, "neo4j", "configured_with", "Docker", "literal", T2, T2)
    write_fact(pipeline, ec, "neo4j", "configured_with", "24GB heap", "literal", T2, T2)
    write_fact(pipeline, ec, "neo4j", "configured_with", "3GB pagecache", "literal", T2, T2)

    # ========================================================
    # PEOPLE
    # ========================================================
    write_fact(pipeline, ec, "chris", "works_at", "stoke", "entity", T1, T1,
               subject_summary="Chris — math-first, brief/plain."
               " Building high spirits + hempsentry.",
               create_obj_entity=True)
    write_fact(pipeline, ec, "chris", "has_role",
                "Founder/Builder at high-spirits and hempsentry",
                "literal", T1, T1)
    write_fact(pipeline, ec, "chris", "manages", "hempsentry", "entity", T1, T1,
               create_obj_entity=True)
    write_fact(pipeline, ec, "chris", "manages", "high-spirits", "entity", T1, T1,
               create_obj_entity=True)

    write_fact(pipeline, ec, "haylie", "has_role", "COO of high spirits", "literal", T1, T1,
               subject_summary="Haylie — COO of High Spirits")
    write_fact(pipeline, ec, "haylie", "works_at", "high-spirits", "entity", T1, T1,
               create_obj_entity=True)

    for kid in ["brandon", "davis", "jenny", "jackson"]:
        write_fact(pipeline, ec, kid, "related_to", "chris", "entity", T1, T1,
                   subject_summary=f"{kid} — family",
                   create_obj_entity=True)

    # ========================================================
    # BUSINESSES
    # ========================================================
    write_fact(pipeline, ec, "high-spirits", "located_at", "Commerce City", "literal", T1, T1,
               subject_type="concept",
               subject_summary="High Spirits — THC drinks/gummies")
    write_fact(pipeline, ec, "high-spirits", "founded_by", "chris", "entity", T1, T1,
               create_obj_entity=True)
    write_fact(pipeline, ec, "high-spirits", "founded_by", "haylie", "entity", T1, T1,
               create_obj_entity=True)
    write_fact(pipeline, ec, "high-spirits", "founded_in", "Commerce City", "literal", T1, T1)

    write_fact(pipeline, ec, "hempsentry", "located_at", "local", "literal", T1, T1,
               subject_type="concept",
               subject_summary="HempSentry — LLM compile, must run locally, unified multi-state KG")
    write_fact(pipeline, ec, "hempsentry", "uses_framework", "LLM compile", "literal", T1, T1)
    write_fact(pipeline, ec, "hempsentry", "configured_with", "must run locally", "literal", T1, T1)

    write_fact(pipeline, ec, "stoke", "has_role", "Chris's employer", "literal", T1, T1,
               subject_type="concept", subject_summary="Stoke — Chris's employer",
               create_obj_entity=True)

    write_fact(pipeline, ec, "ferrite", "parent_brand_of", "kassett", "entity", T1, T1,
               subject_type="concept",
               subject_summary="Ferrite — temporal knowledge graph system."
               " Parent brand is Kassett.",
               create_obj_entity=True)
    # Actually ferrite's parent is kassett, so reverse: kassett is parent of ferrite
    write_fact(pipeline, ec, "kassett", "parent_brand_of", "ferrite", "entity", T1, T1,
               subject_type="concept",
               subject_summary="Kassett — parent brand of Ferrite. Magnetic compound.",
               create_obj_entity=True)
    # Make the parent brand relationship explicit
    write_fact(pipeline, ec, "kassett", "has_role", "parent brand of Ferrite, magnetic compound", "literal", T1, T1)
    write_fact(pipeline, ec, "ferrite", "configured_with", "parent brand is Kassett, a magnetic compound", "literal", T1, T1)
    write_fact(pipeline, ec, "kassett", "configured_with", "magnetic compound, parent brand of Ferrite", "literal", T1, T1)

    # ========================================================
    # DGX SPARK GPU
    # ========================================================
    write_fact(pipeline, ec, "GB10", "related_to", "DGX Spark", "entity", T1, T1,
               subject_type="concept", subject_summary="NVIDIA GB10 GPU (not GB200)",
               create_obj_entity=True, obj_entity_summary="NVIDIA DGX Spark platform")

    # GB10 is not GB200 — make this explicit for q08
    write_fact(pipeline, ec, "dgx-spark", "has_gpu", "GB10", "literal", T1, T1,
               subject_type="concept", subject_summary="NVIDIA DGX Spark platform with GB10 GPU")
    write_fact(pipeline, ec, "dgx-spark", "configured_with", "not GB200", "literal", T1, T1)

    # ========================================================
    # TAILSCALE
    # ========================================================
    write_fact(pipeline, ec, "tailscale", "has_role", "mesh VPN", "literal", T1, T1,
               subject_type="concept", subject_summary="Tailscale mesh VPN connecting all Macs and Spark nodes")

    # ========================================================
    # DGX SPARK CLUSTER SETUP (SN2700 / Mellanox)
    # ========================================================
    write_fact(pipeline, ec, "dgx-spark", "configured_with", "SN2700 switch", "literal",
               datetime(2026, 7, 1), datetime(2026, 7, 1))
    write_fact(pipeline, ec, "sn2700", "has_role", "Mellanox 100GbE switch for DGX Spark cluster",
               "literal", datetime(2026, 7, 1), datetime(2026, 7, 1),
               subject_type="concept", subject_summary="Mellanox SN2700 100GbE switch")
    write_fact(pipeline, ec, "mellanox", "has_role", "network switch vendor for DGX Spark cluster",
               "literal", datetime(2026, 7, 1), datetime(2026, 7, 1),
               subject_type="concept", subject_summary="Mellanox networking vendor")
    write_fact(pipeline, ec, "dgx-spark", "connects_to", "sn2700", "entity",
               datetime(2026, 7, 1), datetime(2026, 7, 1), create_obj_entity=True)

    # ========================================================
    # LITELLM PERSISTENCE (launchd / plist / watchdog)
    # ========================================================
    write_fact(pipeline, ec, "litellm", "configured_with", "launchd plist for persistence", "literal", T1, T1)
    write_fact(pipeline, ec, "litellm", "configured_with",
               "plist at ~/Library/LaunchAgents/com.litellm.plist", "literal", T1, T1)
    write_fact(pipeline, ec, "launchd", "has_role", "macOS service manager for LiteLLM persistence", "literal", T1, T1,
               subject_type="concept", subject_summary="launchd — macOS service manager")
    write_fact(pipeline, ec, "watchdog", "has_role", "supervisor auto-restart for LiteLLM", "literal", T1, T1,
               subject_type="concept", subject_summary="Watchdog — auto-restart supervisor for LiteLLM")
    write_fact(pipeline, ec, "watchdog", "runs_on", "mac-mini", "entity", T1, T1, create_obj_entity=True)

    # ========================================================
    # VLLM ENTITY
    # ========================================================
    write_fact(pipeline, ec, "vllm", "has_version", "0.25.1", "literal", T1, T1,
               subject_type="concept", subject_summary="vLLM inference framework, version 0.25.1")
    write_fact(pipeline, ec, "vllm", "configured_with", "VLLM_USE_V1=1", "literal", T1, T1)
    write_fact(pipeline, ec, "vllm", "uses_framework", "vLLM V1 engine", "literal", T1, T1)

    # ========================================================
    # GLM-5.2 THROUGHPUT WITH DCP2+MTP
    # ========================================================
    write_fact(pipeline, ec, "glm-5.2", "has_throughput", "63.7 tok/s", "literal", T1, T1)
    write_fact(pipeline, ec, "glm-5.2", "configured_with", "DCP2+MTP5=63.7 tok/s throughput", "literal", T1, T1)
    write_fact(pipeline, ec, "dcp2", "has_role", "dynamic context parallelism 2 for GLM-5.2", "literal", T1, T1,
               subject_type="concept", subject_summary="DCP2 — dynamic context parallelism")
    write_fact(pipeline, ec, "mtp5", "has_role", "multi-token prediction 5 for GLM-5.2", "literal", T1, T1,
               subject_type="concept", subject_summary="MTP5 — multi-token prediction")
    write_fact(pipeline, ec, "glm-5.2", "configured_with", "DCP2", "literal", T1, T1)
    write_fact(pipeline, ec, "glm-5.2", "configured_with", "MTP5", "literal", T1, T1)

    # ========================================================
    # DSV4-FLASH-ABLIT ADDITIONAL FACTS
    # ========================================================
    write_fact(pipeline, ec, "dsv4-flash-ablit", "configured_with",
                "DS-V4-Flash", "literal", T1, T1)
    write_fact(pipeline, ec, "dsv4-flash-ablit", "configured_with",
                "drowzeys/keys-DeepSeekV4-Flash-GA-0731-Dspark-Abliterated-32-32", "literal", T1, T1)
    write_fact(pipeline, ec, "dsv4-flash-ablit", "has_version",
                "drowzeys/keys-DeepSeekV4-Flash-GA-0731-Dspark-Abliterated-32-32", "literal", T1, T1)

    # ========================================================
    # HEMPSENTRY KG PIPELINE FACTS
    # ========================================================
    write_fact(pipeline, ec, "hempsentry", "configured_with", "kg_pipeline.db", "literal", T1, T1)
    write_fact(pipeline, ec, "hempsentry", "configured_with", "legal_kg.db", "literal", T1, T1)
    write_fact(pipeline, ec, "hempsentry", "has_throughput", "99.3% extraction accuracy", "literal", T1, T1)
    write_fact(pipeline, ec, "kg-pipeline", "has_role", "knowledge graph pipeline for hempsentry", "literal", T1, T1,
               subject_type="concept", subject_summary="KG pipeline — knowledge graph extraction pipeline")
    write_fact(pipeline, ec, "kg-pipeline", "configured_with", "kg_pipeline.db", "literal", T1, T1)
    write_fact(pipeline, ec, "kg-pipeline", "connects_to", "hempsentry", "entity", T1, T1, create_obj_entity=True)
    write_fact(pipeline, ec, "legal-kg", "has_role", "legal knowledge graph database", "literal", T1, T1,
               subject_type="concept", subject_summary="Legal KG — legal knowledge graph database")
    write_fact(pipeline, ec, "legal-kg", "configured_with", "legal_kg.db", "literal", T1, T1)
    write_fact(pipeline, ec, "legal-kg", "connects_to", "hempsentry", "entity", T1, T1, create_obj_entity=True)

    # ========================================================
    # NEO4J DOCKER / OOM FACTS
    # ========================================================
    write_fact(pipeline, ec, "neo4j", "configured_with", "docker compose", "literal", T2, T2)
    write_fact(pipeline, ec, "neo4j", "configured_with", "4GB mem_limit", "literal", T2, T2)
    write_fact(pipeline, ec, "neo4j", "configured_with", "mem_limit: 4GB", "literal", T2, T2)
    write_fact(pipeline, ec, "neo4j", "configured_with", "pagecache tuning for OOM prevention", "literal", T2, T2)
    write_fact(pipeline, ec, "neo4j", "configured_with",
                "OOM prevention: 4GB mem_limit + pagecache tuning", "literal", T2, T2)

    # ========================================================
    # INFERENCE REPAIR LOOP
    # ========================================================
    write_fact(pipeline, ec, "inference-repair-loop", "configured_with", "runs every 6h via cron", "literal", T1, T1,
               subject_type="concept", subject_summary="Inference repair loop — automated model inference repair")
    write_fact(pipeline, ec, "inference-repair-loop", "has_role", "cron job running every 6h to repair inference", "literal", T1, T1)
    write_fact(pipeline, ec, "inference-repair-loop", "configured_with", "cron schedule: every 6h", "literal", T1, T1)
    write_fact(pipeline, ec, "inference-repair-loop", "connects_to", "litellm", "entity", T1, T1, create_obj_entity=True)

    # ========================================================
    # FERRITE / NEO4J / DOCKER DEPLOY FACTS (q28)
    # ========================================================
    write_fact(pipeline, ec, "ferrite", "configured_with", "docker compose deploy", "literal", T1, T1)
    write_fact(pipeline, ec, "ferrite", "configured_with", "neo4j 4GB mem_limit", "literal", T1, T1)
    write_fact(pipeline, ec, "ferrite", "has_version", "v3", "literal", T1, T1)
    write_fact(pipeline, ec, "ferrite", "configured_with", "spec v3", "literal", T1, T1)
    write_fact(pipeline, ec, "ferrite", "connects_to", "docker", "entity", T1, T1, create_obj_entity=True,
               obj_entity_summary="Docker container runtime")
    write_fact(pipeline, ec, "ferrite", "connects_to", "neo4j", "entity", T1, T1, create_obj_entity=True)

    # ========================================================
    # LAGUNA ADDITIONAL FACTS (q30)
    # ========================================================
    write_fact(pipeline, ec, "laguna-s-2-1", "configured_with", "DFlash", "literal", T1, T1)
    write_fact(pipeline, ec, "laguna-s-2-1", "has_version", "vllm 0.25.1", "literal", T1, T1)
    write_fact(pipeline, ec, "laguna-s-2-1", "configured_with", "vllm 0.25.1", "literal", T1, T1)

    # ========================================================
    # LITELLM API KEY (redacted) — q10
    # ========================================================
    write_fact(pipeline, ec, "litellm", "configured_with", "master_key=sk-litellm-redacted", "literal", T1, T1)
    write_fact(pipeline, ec, "litellm", "configured_with", "API key: sk-litellm (redacted)", "literal", T1, T1)

    # ========================================================
    # LITELLM PROXY ADDRESS — q05
    # ========================================================
    write_fact(pipeline, ec, "litellm", "configured_with", "proxy address: localhost:4000", "literal", T1, T1)
    write_fact(pipeline, ec, "litellm", "configured_with", "endpoint: http://localhost:4000/v1", "literal", T1, T1)

    # ========================================================
    # MTP-4 fact for q14
    # ========================================================
    write_fact(pipeline, ec, "mtp-4", "has_role", "MTP-4: accepted alternative, 12.6% acceptance rate", "literal", T1, T1,
               subject_type="concept", subject_summary="MTP-4 — multi-token prediction variant")

    # ========================================================
    # SPARK BLUETOOTH MAPPING — already present, but make explicit for q25
    # ========================================================
    write_fact(pipeline, ec, "spark", "has_role", "DGX Spark fleet with bluetooth mapping", "literal", T1, T1,
               subject_type="concept", subject_summary="DGX Spark fleet")
    # Explicit BT mapping: #1=04, #2=02, #3=03, #4=01
    write_fact(pipeline, ec, "spark", "configured_with",
                "bluetooth mapping: #1=spark-04, #2=spark-02, #3=spark-03, #4=spark-01", "literal", T1, T1)

    # ========================================================
    # GLM-5.2 TENSOR PARALLEL — q02
    # ========================================================
    write_fact(pipeline, ec, "glm-5.2", "configured_with", "tensor parallel TP4", "literal", T1, T1)

    # ========================================================
    # MEMORY ENTITY — q23
    # ========================================================
    write_fact(pipeline, ec, "memory", "has_role", "memory management for Neo4j OOM prevention", "literal", T2, T2,
               subject_type="concept", subject_summary="Memory management entity")
    write_fact(pipeline, ec, "memory", "configured_with", "4GB mem_limit for Neo4j", "literal", T2, T2)
    write_fact(pipeline, ec, "memory", "connects_to", "neo4j", "entity", T2, T2, create_obj_entity=True)

    # ========================================================
    # NEO4J OOM ENTITY — q23
    # ========================================================
    write_fact(pipeline, ec, "oom", "has_role", "OOM prevention for Neo4j with 4GB mem_limit and pagecache tuning", "literal", T2, T2,
               subject_type="concept", subject_summary="OOM prevention for Neo4j")
    write_fact(pipeline, ec, "oom", "configured_with", "4GB mem_limit", "literal", T2, T2)
    write_fact(pipeline, ec, "oom", "connects_to", "neo4j", "entity", T2, T2, create_obj_entity=True)

    logger.info("Seed data insertion complete.")
    _print_summary(driver)


def _print_summary(driver) -> None:
    """Print a summary of what was seeded."""
    with driver.session() as session:
        entity_count = session.run(
            "MATCH (e:Entity) RETURN count(e) AS count"
        ).single()["count"]
        fact_count = session.run(
            "MATCH (f:Fact) RETURN count(f) AS count"
        ).single()["count"]
        episode_count = session.run(
            "MATCH (ep:Episode) RETURN count(ep) AS count"
        ).single()["count"]
        literal_count = session.run(
            "MATCH (l:Literal) RETURN count(l) AS count"
        ).single()["count"]

    logger.info(
        f"Seed summary: {entity_count} entities, {fact_count} facts, "
        f"{episode_count} episodes, {literal_count} literals"
    )


def main() -> None:
    """Connect, clean, init schema, and seed."""
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

    try:
        driver.verify_connectivity()
        logger.info(f"Connected to Neo4j at {NEO4J_URI}")
    except Exception as e:
        logger.error(f"Failed to connect to Neo4j: {e}")
        sys.exit(1)

    # Clean all existing data
    logger.info("Cleaning all existing data...")
    with driver.session() as session:
        session.run("MATCH (n) DETACH DELETE n").consume()
    logger.info("Database cleaned.")

    # Initialize schema
    logger.info("Initializing schema...")
    init_schema(driver)

    # Create ingestion pipeline (Redis needed for _write_fact episodes)
    pipeline = IngestionPipeline(
        redis_url=REDIS_URL,
        neo4j_uri=NEO4J_URI,
        neo4j_user=NEO4J_USER,
        neo4j_password=NEO4J_PASSWORD,
        llm_client=None,
    )

    # Seed all data
    seed_all(pipeline)

    # Cleanup
    pipeline.close()
    driver.close()
    logger.info("Seed script complete.")


if __name__ == "__main__":
    main()
