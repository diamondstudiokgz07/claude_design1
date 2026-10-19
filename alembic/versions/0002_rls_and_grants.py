"""Row Level Security по app.venue_id, права роли приложения, неизменяемый audit_log

Revision ID: 0002
Revises: 0001
"""
import os

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

APP_ROLE = os.environ.get("DOSTUK_APP_DB_ROLE", "dostuk_app")

TENANT_TABLES = [
    "audit_log", "cash_movements", "categories", "devices", "discounts", "files", "fiscal_receipts",
    "halls", "idempotency_keys", "menu_item_modifier_groups", "menu_item_translations", "menu_items",
    "modifier_groups", "modifiers", "order_counters", "order_item_modifiers", "order_items", "orders",
    "outbox_events", "payments", "print_jobs", "printers", "refresh_tokens", "role_permissions", "roles",
    "shifts", "staff", "staff_stations", "stations", "tables", "venue_settings",
]


def upgrade() -> None:
    op.execute("""
        CREATE OR REPLACE FUNCTION app_venue() RETURNS uuid LANGUAGE sql STABLE AS
        $$ SELECT NULLIF(current_setting('app.venue_id', true), '')::uuid $$;
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION app_bypass() RETURNS boolean LANGUAGE sql STABLE AS
        $$ SELECT coalesce(current_setting('app.bypass_rls', true), '') = 'on' $$;
    """)

    for t in TENANT_TABLES:
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {t} "
            f"USING (app_bypass() OR venue_id = app_venue()) "
            f"WITH CHECK (app_bypass() OR venue_id = app_venue())"
        )

    op.execute("ALTER TABLE venues ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE venues FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON venues "
        "USING (app_bypass() OR id = app_venue()) WITH CHECK (app_bypass() OR id = app_venue())"
    )

    # Журнал нельзя изменить или удалить
    op.execute("""
        CREATE OR REPLACE FUNCTION audit_log_immutable() RETURNS trigger LANGUAGE plpgsql AS
        $$ BEGIN RAISE EXCEPTION 'audit_log is append-only'; END $$;
    """)
    op.execute("""
        CREATE TRIGGER trg_audit_log_immutable BEFORE UPDATE OR DELETE ON audit_log
        FOR EACH ROW EXECUTE FUNCTION audit_log_immutable();
    """)

    op.execute(f"""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
            EXECUTE 'GRANT USAGE ON SCHEMA public TO {APP_ROLE}';
            EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}';
            EXECUTE 'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}';
            EXECUTE 'REVOKE UPDATE, DELETE, TRUNCATE ON audit_log FROM {APP_ROLE}';
            EXECUTE 'REVOKE ALL ON alembic_version FROM {APP_ROLE}';
            EXECUTE 'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {APP_ROLE}';
            EXECUTE 'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {APP_ROLE}';
          END IF;
        END $$;
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_audit_log_immutable ON audit_log")
    op.execute("DROP FUNCTION IF EXISTS audit_log_immutable()")
    for t in TENANT_TABLES + ["venues"]:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {t}")
        op.execute(f"ALTER TABLE {t} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY")
    op.execute("DROP FUNCTION IF EXISTS app_bypass()")
    op.execute("DROP FUNCTION IF EXISTS app_venue()")
