from dotenv import load_dotenv
load_dotenv()

import os
import asyncio
import asyncpg
from openai import OpenAI
from utils.pg_connections import get_connection_url_pg

# Validate required environment variables
REQUIRED_ENV_VARS = [
    "OPENAI_API_KEY",
    "OPENAI_VEC_STORE_ID",
]

missing_vars = [var for var in REQUIRED_ENV_VARS if not os.getenv(var)]
if missing_vars:
    raise EnvironmentError(f"Missing required environment variables: {', '.join(missing_vars)}")

pool = None
pool_size = int(os.environ.get("LLM_POSTGRES_POOL_SIZE", "10"))
# Initialize the OpenAI client
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
vector_store_id = os.environ.get("OPENAI_VEC_STORE_ID")

async def main():
    print("--START--")
    print("Creating Postgres connection pool...")

    global pool
    pool = await asyncpg.create_pool(
        get_connection_url_pg(),
        min_size=pool_size,
        max_size=pool_size + 3,
    )
    print("Postgres connection pool created")
    
    async with pool.acquire() as conn:
        db_articles = await conn.fetch(
            "SELECT fd_article_id, openai_file_id, last_synced_at, is_deleted FROM freshdesk_article_sync_status"
        )
        db_articles_map = {record['fd_article_id']: record for record in db_articles}
        processed_article_ids = set()

        for article_id, article in db_articles_map.items():
            openai_file_id = article['openai_file_id']
            print(f"---Deleting article ID: {article_id}---")

            # Delete from vectorstore and OpenAI files
            try:
                print(f"Deleting article from vectorstore")
                client.vector_stores.files.delete(
                    vector_store_id=vector_store_id, file_id=openai_file_id
                )
                print(f"Deleting OpenAI file")
                client.files.delete(file_id=openai_file_id)
            except Exception as e:
                print(f"Error deleting article ID {article_id}: {e}")
                continue

            processed_article_ids.add(article_id)

        # Delete articles from the database
        if processed_article_ids:
            print("Deleting articles from DB...")
            await conn.execute(
                """
                DELETE FROM freshdesk_article_sync_status
                WHERE fd_article_id = ANY($1)
                """,
                list(processed_article_ids),
            )
            print("Articles deleted from DB")

        print("Processing completed.")
        print(f"Total deleted articles: {len(processed_article_ids)}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(e)