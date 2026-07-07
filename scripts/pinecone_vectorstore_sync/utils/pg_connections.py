import os
import psycopg2
import psycopg2.pool

if (
    os.environ.get("LLM_POSTGRES_DATABASENAME") is None
    or os.environ.get("LLM_POSTGRES_DATABASENAME") == ""
):
    raise Exception("Invalid LLM_POSTGRES_DATABASENAME in env")
databasename = os.environ["LLM_POSTGRES_DATABASENAME"]
if (
    os.environ.get("LLM_POSTGRES_USERNAME") is None
    or os.environ.get("LLM_POSTGRES_USERNAME") == ""
):
    raise Exception("Invalid LLM_POSTGRES_USERNAME in env")
username = os.environ["LLM_POSTGRES_USERNAME"]
if (
    os.environ.get("LLM_POSTGRES_PASSWORD") is None
    or os.environ.get("LLM_POSTGRES_PASSWORD") == ""
):
    raise Exception("Invalid LLM_POSTGRES_PASSWORD in env")
password = os.environ["LLM_POSTGRES_PASSWORD"]
if (
    os.environ.get("LLM_POSTGRES_HOST") is None
    or os.environ.get("LLM_POSTGRES_HOST") == ""
):
    raise Exception("Invalid LLM_POSTGRES_HOST in env")
host = os.environ["LLM_POSTGRES_HOST"]
port = None
if (
    os.environ.get("LLM_POSTGRES_PORT") is None
    or os.environ.get("LLM_POSTGRES_PORT") == ""
):
    port = "5432"


def get_connection_url_pg():
    return f'postgresql://{os.environ.get("LLM_POSTGRES_USERNAME")}:{os.environ.get("LLM_POSTGRES_PASSWORD")}@{os.environ.get("LLM_POSTGRES_HOST")}:{os.environ.get("LLM_POSTGRES_PORT")}/{os.environ.get("LLM_POSTGRES_DATABASENAME")}'