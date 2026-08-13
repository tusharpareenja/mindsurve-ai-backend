from sqlalchemy import inspect, text

from app.db.session import get_engine

eng = get_engine()
with eng.connect() as c:
    tables = inspect(c).get_table_names()
    print("has_study_generation_runs", "study_generation_runs" in tables)
    print(
        "alembic_version",
        c.execute(text("select version_num from alembic_version")).fetchall(),
    )
    if "study_generation_runs" in tables:
        n = c.execute(text("select count(*) from study_generation_runs")).scalar()
        print("row_count", n)
