from hashlib import sha256
from uuid import uuid4

from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from alembic import command


def test_full_migration_chain_preserves_phase_1b_rows(database_url: str, monkeypatch) -> None:
    schema = f"test_extraction_migrations_{uuid4().hex}"
    admin = create_engine(database_url, isolation_level="AUTOCOMMIT", hide_parameters=True)
    with admin.connect() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    url = (
        make_url(database_url)
        .update_query_dict({"options": f"-csearch_path={schema} -ctimezone=UTC"})
        .render_as_string(hide_password=False)
    )
    engine = create_engine(url, hide_parameters=True)
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config("alembic.ini")
    document_id, finding_id, evidence_id = uuid4(), uuid4(), uuid4()
    try:
        command.upgrade(config, "0001_raw_documents")
        with engine.begin() as connection:
            connection.execute(
                text("""
                INSERT INTO raw_documents(id, source_name, document_type, raw_text, content_hash)
                VALUES (:id, 'manual', 'report', :raw, :hash)
            """),
                {"id": document_id, "raw": "Report.", "hash": sha256(b"Report.").hexdigest()},
            )
        command.upgrade(config, "0002_findings_taxonomy")
        with engine.begin() as connection:
            connection.execute(
                text("""
                INSERT INTO security_findings (
                    id, raw_document_id, title, severity, canonical_category, source_category
                ) VALUES (:id, :document, 'Manual finding', 'LOW', 'OTHER', ' Source tag ')
            """),
                {"id": finding_id, "document": document_id},
            )
            connection.execute(
                text("""
                INSERT INTO finding_evidence(
                    id, finding_id, source_excerpt, start_offset, end_offset
                )
                VALUES (:id, :finding, 'Report.', 0, 7)
            """),
                {"id": evidence_id, "finding": finding_id},
            )

        def snapshot():
            with engine.connect() as connection:
                return {
                    table: dict(connection.execute(text(f"SELECT * FROM {table}")).mappings().one())
                    for table in ["raw_documents", "security_findings", "finding_evidence"]
                }

        original = snapshot()
        command.upgrade(config, "head")
        command.check(config)
        upgraded = snapshot()
        assert upgraded["security_findings"].pop("extraction_run_id") is None
        assert upgraded == original
        with engine.begin() as connection:
            connection.execute(
                text("""
                INSERT INTO extraction_runs(
                    id, raw_document_id, provider, model, prompt_version, schema_version
                )
                VALUES (:id, :document, 'fixture', 'fixture', 'prompt-v1', 'schema-v1')
            """),
                {"id": uuid4(), "document": document_id},
            )
        command.downgrade(config, "0002_findings_taxonomy")
        assert snapshot() == original
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT to_regclass('extraction_runs')")) is None
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == "0002_findings_taxonomy"
            )
        command.upgrade(config, "head")
        command.check(config)
        reupgraded = snapshot()
        assert reupgraded["security_findings"].pop("extraction_run_id") is None
        assert reupgraded == original
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT COUNT(*) FROM extraction_runs")) == 0
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == "0003_extraction_runs"
            )
    finally:
        engine.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
