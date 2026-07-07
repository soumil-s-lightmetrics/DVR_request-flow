from dotenv import load_dotenv
load_dotenv()

import os
import asyncio
import asyncpg
import aiohttp
from datetime import datetime, timezone
import io
import re
import json
from openai import OpenAI
import argparse
from html_to_markdown import convert_to_markdown

# Validate required environment variables
REQUIRED_ENV_VARS = [
    "FRESHDESK_API_BASE_URL",
    "FRESHDESK_API_KEY",
    "OPENAI_API_KEY",
    "OPENAI_VEC_STORE_ID",
    "LLM_POSTGRES_USERNAME",
    "LLM_POSTGRES_PASSWORD",
    "LLM_POSTGRES_HOST",
    "LLM_POSTGRES_PORT",
    "LLM_POSTGRES_DATABASENAME",
]

missing_vars = [var for var in REQUIRED_ENV_VARS if not os.getenv(var)]
if missing_vars:
    raise EnvironmentError(f"Missing required environment variables: {', '.join(missing_vars)}")

# Environment variables
FRESHDESK_API_BASE_URL = os.getenv("FRESHDESK_API_BASE_URL")
API_KEY = os.getenv("FRESHDESK_API_KEY")
AUTH = aiohttp.BasicAuth(API_KEY, 'X')
ALLOWED_CATEGORIES = (
    "General", "Hardware", "Events and Violations", "Companion Apps", "Master Portal",
    "Fleet Portal", "Rebranded Portal", "Device Application", "Backend and API", "SDK",
    "Intermediate Server", "Lisa Chat Bot",
)
pool = None
pool_size = int(os.environ.get("LLM_POSTGRES_POOL_SIZE", "10"))
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
vector_store_id = os.environ.get("OPENAI_VEC_STORE_ID")

# Logging utilities
previous_millis = datetime.now().timestamp()

def logtimedelta():
    global previous_millis
    current_millis = datetime.now().timestamp()
    val = round(current_millis - previous_millis, 3)
    previous_millis = current_millis
    return val

def logprefix():
    return f"{datetime.now().isoformat()}({logtimedelta()}) "

def get_connection_url_pg():
    return f'postgresql://{os.environ.get("LLM_POSTGRES_USERNAME")}:{os.environ.get("LLM_POSTGRES_PASSWORD")}@{os.environ.get("LLM_POSTGRES_HOST")}:{os.environ.get("LLM_POSTGRES_PORT")}/{os.environ.get("LLM_POSTGRES_DATABASENAME")}'

# Async HTTP request with retries
async def fetch_with_retry(url, method="GET", params=None, data=None, headers=None, auth=None, retries=3):
    async with aiohttp.ClientSession() as session:
        for attempt in range(retries):
            try:
                async with session.request(method, url, params=params, data=data, headers=headers, auth=auth) as response:
                    if response.status == 429:  # Rate limit
                        retry_after = int(response.headers.get("Retry-After", 10))
                        print(f"{logprefix()}Rate limit hit. Retrying after {retry_after} seconds...")
                        await asyncio.sleep(retry_after)
                        continue
                    response.raise_for_status()
                    return await response.json()
            except aiohttp.ClientError as e:
                print(f"{logprefix()}HTTP error (attempt {attempt + 1}/{retries}): {e}")
                if attempt < retries - 1:
                    await asyncio.sleep(2)
                else:
                    raise

# Fetch categories, folders, and articles
async def get_categories():
    print(f"{logprefix()}Fetching categories...")
    url = f"{FRESHDESK_API_BASE_URL}/api/v2/solutions/categories"
    return await fetch_with_retry(url, auth=AUTH)

async def get_folders(category_id):
    print(f"{logprefix()}Fetching folders for category ID {category_id}...")
    url = f"{FRESHDESK_API_BASE_URL}/api/v2/solutions/categories/{category_id}/folders"
    return await fetch_with_retry(url, auth=AUTH)

async def get_articles(folder_id):
    all_articles = []
    page = 1
    while True:
        print(f"{logprefix()}Fetching articles for folder ID {folder_id}, page {page}...")
        url = f"{FRESHDESK_API_BASE_URL}/api/v2/solutions/folders/{folder_id}/articles"
        articles = await fetch_with_retry(url, params={"page": page}, auth=AUTH)
        if not articles:
            break
        all_articles.extend(articles)
        page += 1
    return all_articles

async def get_all_articles():
    print(f"{logprefix()}Fetching all articles from Freshdesk...")
    all_articles = []
    categories = await get_categories()
    allowed_categories = [cat for cat in categories if cat['name'] in ALLOWED_CATEGORIES]

    for cat in allowed_categories:        
        folders = await get_folders(cat['id'])
        for folder in folders:
            articles = await get_articles(folder['id'])
            for article in articles:
                article['category_id'] = cat['id']
                article['category_name'] = cat['name']
            all_articles.extend(articles)
    return all_articles

async def get_article_by_id(article_id):
    print(f"{logprefix()}Fetching article by ID {article_id}...")
    url = f"{FRESHDESK_API_BASE_URL}/api/v2/solutions/articles/{article_id}"
    return await fetch_with_retry(url, auth=AUTH)

async def create_mardown_file(item):
    html_content = f"""<!DOCTYPE html>
    <html>
    <head>
        <title>{item["title"]}</title>
    </head>
    <body>
        {item["content"]}
    </body>
    </html>"""
    markdown = convert_to_markdown(html_content)
    filename = re.sub('[^A-Za-z0-9]+', '_', item['title']) + '.md'
    file_buffer = io.BytesIO(markdown.encode('utf-8'))
    file_buffer.name = filename

    file_response = client.files.create(file=file_buffer, purpose="assistants")
    return file_response.id, filename

async def update_metadata(file_id, metadata):
    url = f"https://api.openai.com/v1/vector_stores/{vector_store_id}/files/{file_id}"
    payload = json.dumps({"attributes": metadata})
    headers = {
        'Authorization': f'Bearer {os.environ.get("OPENAI_API_KEY")}',
        'Content-Type': 'application/json'
    }
    await fetch_with_retry(url, method="POST", data=payload, headers=headers)

async def add_document(item):
    print(f"{logprefix()}Adding document- {item['title']}")
    file_id, filename = await create_mardown_file(item)   
    # Add the file to the vector store with metadata
    client.vector_stores.file_batches.create(
        vector_store_id=vector_store_id,
        file_ids=[file_id]
    )
    metadata = {
        "fd_article_url": f"https://lightmetrics.freshdesk.com/a/solutions/articles/{item['article_id']}",
        "id": item["article_id"]
    }
    # Update metadata for the file
    print(f"{logprefix()}Updating metadata for document- {item['title']}")
    await update_metadata(file_id, metadata)
    return {"id": file_id, "filename": filename}

async def delete_document(file_id):
    try:
        print(f"{logprefix()}Deleting old file- {file_id}")
        client.vector_stores.files.delete(vector_store_id=vector_store_id, file_id=file_id)
        client.files.delete(file_id=file_id)
    except Exception as e:
        print(f"Error deleting document ID {file_id}: {e}")

# Sync article with vector store
async def sync_article_with_vectorstore(article, db_entry=None):
    if article["status"] != 2 and article['category_name'] != "Lisa Chat Bot":  # Skip articles that are not published unless category is Lisa Chat Bot
        print(f"{logprefix()}Skipping article ID {article['id']} as it is not published")
        return
    
    article_id = article['id']
    openai_doc_content = {
        "title": article['title'],
        "content": article["description"],
        "article_id": article["id"]
    }
    
    print(f"{logprefix()}---Syncing article ID: {article_id}---")
    
    if db_entry is None:  # New article
        print(f"{logprefix()}Adding new article to vectorstore")
        add_document_response = await add_document(openai_doc_content)
        if not add_document_response:
            print(f"{logprefix()}Failed to add document. Skipping...")
            return
        
        openai_file_id = add_document_response['id']
        print(f"{logprefix()}Adding new article to DB: {openai_file_id}")
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO freshdesk_article_sync_status (fd_article_id, openai_file_id, last_synced_at, is_deleted, filename)
                VALUES ($1, $2, $3, $4, $5)
                """,
                article_id, openai_file_id, datetime.now(timezone.utc), False, add_document_response['filename']
            )
    else:  # Existing article
        last_synced_at = db_entry['last_synced_at'].replace(tzinfo=timezone.utc)
        article_updated_at = datetime.fromisoformat(article['updated_at'].replace('Z', '+00:00'))
        
        if article_updated_at > last_synced_at:  # Updated article
            print(f"{logprefix()}Updating article in vectorstore")
            add_document_response = await add_document(openai_doc_content)
            if not add_document_response:
                print(f"{logprefix()}Failed to update document. Skipping...")
                return

            # Delete the old file from vector store and OpenAI
            old_file_id = db_entry['openai_file_id']
            await delete_document(old_file_id)
            
            openai_file_id = add_document_response['id']
            print(f"{logprefix()}Updating article in DB: {openai_file_id}")
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE freshdesk_article_sync_status
                    SET openai_file_id = $1, last_synced_at = $2, is_deleted = $3, filename = $4
                    WHERE fd_article_id = $5
                    """,
                    openai_file_id, datetime.now(timezone.utc), False, add_document_response['filename'], article_id
                )
        else:
            print(f"{logprefix()}Article is up to date. No action needed.")

# Process articles
async def process_articles(all_articles, mark_deleted=False):
    async with pool.acquire() as conn:
        db_articles = await conn.fetch("SELECT fd_article_id, openai_file_id, last_synced_at, is_deleted FROM freshdesk_article_sync_status")
    
    db_articles_map = {record['fd_article_id']: record for record in db_articles}
    processed_article_ids = set()

    for article in all_articles:
        article_id = article['id']
        await sync_article_with_vectorstore(article, db_articles_map.get(article_id))
        processed_article_ids.add(article['id'])

    # Mark deleted articles
    if mark_deleted:  
        for db_article_id, db_entry in db_articles_map.items():
            if db_article_id not in processed_article_ids and not db_entry['is_deleted']:
                async with pool.acquire() as conn:
                    await conn.execute(
                        """
                        UPDATE freshdesk_article_sync_status
                        SET is_deleted = $1
                        WHERE fd_article_id = $2
                        """,
                        True, db_article_id
                    )

# Main function
async def main():
    parser = argparse.ArgumentParser(description="Sync Freshdesk articles with OpenAI vector store.")
    parser.add_argument(
        "--article-ids",
        type=str,
        help="Comma-separated list of article IDs to sync. If not provided, all articles will be synced.",
    )
    args = parser.parse_args()

    print(f"{logprefix()}--START--")
    global pool
    pool = await asyncpg.create_pool(get_connection_url_pg(), min_size=pool_size, max_size=pool_size + 3)

    try:
        if args.article_ids:
            article_ids = set(map(int, args.article_ids.split(",")))
            print(f"{logprefix()}Syncing specific articles: {article_ids}")
            filtered_articles = []
            for article_id in article_ids:
                article = await get_article_by_id(article_id)
                if article:
                    filtered_articles.append(article)
            print(f"{logprefix()}Total articles to process: {len(filtered_articles)}")
            await process_articles(filtered_articles, False)
        else:
            all_articles = await get_all_articles()
            print(f"{logprefix()}Total articles fetched: {len(all_articles)}")
            await process_articles(all_articles, True)

        print(f"{logprefix()}Processing completed.")
    finally:
        try:
            await asyncio.wait_for(pool.close(), timeout=60)
            print(f"{logprefix()}Postgres connection pool closed.")
        except asyncio.TimeoutError:
            print(f"{logprefix()}Timeout while closing Postgres connection pool. Check if you have any unreleased connections left.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(e)