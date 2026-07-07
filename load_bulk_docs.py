from dotenv import load_dotenv

load_dotenv()

import os
from concurrent.futures import ThreadPoolExecutor
from langchain.docstore.document import Document
from langchain.text_splitter import RecursiveCharacterTextSplitter
from utils.pg_connections import get_connection_url_pg
from utils.vectorstore import new_vectorstore
from urllib.parse import urlparse
from unstructured.partition.html import partition_html
import datetime
import json
import sys
import asyncio
import asyncpg
import boto3
import zipfile

pool = None
previous_millis = datetime.datetime.now().timestamp()


class VectorstorePool:
    def __init__(self, pool_size, internal=False):
        self.pool_size = pool_size
        self.objects = (
            [
                new_vectorstore(os.getenv("INTERNAL_DOCS_COLLECTION"))
                for i in range(pool_size)
            ]
            if internal
            else [new_vectorstore() for i in range(pool_size)]
        )
        self.available = asyncio.Queue()
        for obj in self.objects:
            self.available.put_nowait(obj)

    async def get_object(self):
        obj = await self.available.get()
        return obj

    def release_object(self, obj):
        self.available.put_nowait(obj)


pool_size = int(os.environ.get("LLM_POSTGRES_POOL_SIZE", "10"))
vectorstore_pool = VectorstorePool(pool_size)
int_vectorstore_pool = VectorstorePool(pool_size, True)
executor = ThreadPoolExecutor(pool_size)


def logtimedelta():
    global previous_millis
    current_millis = datetime.datetime.now().timestamp()
    val = round(current_millis - previous_millis, 3)
    previous_millis = current_millis
    return val


def logprefix():
    return f"{datetime.datetime.now().isoformat()}({logtimedelta()}) "


# Adds category-folder to the database
# Returns the row ID
async def add_category_folder(category, folder):
    global pool
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                try:
                    insert_category_folder = """INSERT INTO public.llm_category_folder
                    (category, folder) VALUES ($1, $2)
                    ON CONFLICT DO NOTHING RETURNING ID AS category_folder_id;"""
                    cur = await conn.cursor(insert_category_folder, category, folder)
                    result = await cur.fetchrow()
                    if result is None:
                        cur1 = await conn.cursor(
                            "SELECT ID FROM public.llm_category_folder WHERE category=$1 AND folder=$2;",
                            category,
                            folder,
                        )
                        category_folder_id = (await cur1.fetchrow())["id"]
                        print(
                            f"{logprefix()}Category-folder already exists- ID: {category_folder_id}"
                        )
                        return category_folder_id
                    else:
                        category_folder_id = result["category_folder_id"]
                        print(
                            f"{logprefix()}Added category-folder- ID: {category_folder_id}"
                        )
                        return category_folder_id
                except Exception as e:
                    print(e)
    except Exception as e:
        print(e)


# Adds document to the database
# Splits the document into chunks, then indexes all chunks as vector embeddings
async def add_document(category, category_folder_id, title, text, html_data, fd_article_id):
    global pool
    global vectorstore_pool
    global int_vectorstore_pool
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                insert_source_docs = """INSERT INTO public.llm_source_docs
                    (TITLE, CATEGORY_FOLDER_ID, TEXT_DATA, HTML_DATA) VALUES ($1,$2,$3,$4) RETURNING ID
                    AS SOURCE_ID;"""
                print(f"{logprefix()}About to add document- {title}. Len: {len(text)}")
                source_id = await conn.fetchval(
                    insert_source_docs, title, category_folder_id, text, html_data
                )

                text_chunks = get_unstructured_chunks(html_data)
                document_chunks = list(
                    map(
                        lambda text_chunk: Document(
                            page_content=text_chunk, metadata={"source": source_id, "fd_article": "https://lightmetrics.freshdesk.com/a/solutions/articles/"
                                + str(fd_article_id)}
                        ),
                        text_chunks,
                    )
                )

                print(
                    f"{logprefix()}Added document. About to add document via vector store- source ID: {source_id}. {len(text_chunks)} chunks"
                )
                internal_category = category not in (
                    "General",
                    "Hardware",
                    "Events and Violations",
                    "Companion Apps",
                    "Master Portal",
                    "Fleet Portal",
                    "Rebranded Portal",
                    "Device Application",
                    "Backend and API",
                    "SDK",
                    "Intermediate Server",
                )
                if internal_category and os.environ.get(
                    "INTERNAL_DOCS_COLLECTION", None
                ):
                    vectorstore = await int_vectorstore_pool.get_object()
                    asyncio.get_running_loop().run_in_executor(
                        executor, lambda: vectorstore.add_documents(document_chunks)
                    )
                    int_vectorstore_pool.release_object(vectorstore)
                else:
                    vectorstore = await vectorstore_pool.get_object()
                    asyncio.get_running_loop().run_in_executor(
                        executor, lambda: vectorstore.add_documents(document_chunks)
                    )
                    vectorstore_pool.release_object(vectorstore)

                print(
                    f"{logprefix()}Added vector store doc. About to update source_docs_id- source ID: {source_id}. {len(text_chunks)} chunks"
                )
                await conn.execute(
                    "UPDATE public.langchain_pg_embedding SET source_docs_id=$1 WHERE cmetadata->>'source' = $2;",
                    source_id,
                    str(source_id),
                )
                print(
                    f"{logprefix()}Indexed document- source ID: {source_id}. {len(text_chunks)} chunks"
                )
                await asyncio.sleep(0.1)
    except Exception as e:
        print(e)


def get_text_chunks(text):
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000, chunk_overlap=200, length_function=len
    )
    chunks = text_splitter.split_text(text)
    return chunks

def get_unstructured_chunks(html_content):
    elements = partition_html(text=html_content)
    element_dict = [el.to_dict() for el in elements]
    chunks = [el['text'] for el in element_dict]
    return chunks

# Pulls the zipped data from S3 bucket for the given url
# extracs the data from zip file
async def get_data(path):
    url_obj = urlparse(path, allow_fragments=False)

    s3 = boto3.client("s3")
    s3.download_file(url_obj.netloc, url_obj.path.strip("/"), "data.zip")

    with zipfile.ZipFile("./data.zip", "r") as zip_ref:
        zip_ref.extractall("./data")
    return "./data/Solutions.json"


# Truncates the embeddings and source docs tables to avoid duplication
async def truncate_tables():
    global pool
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("TRUNCATE public.langchain_pg_embedding CASCADE")
            print(f"{logprefix()}Truncated embeddings table")
            await conn.execute("TRUNCATE public.llm_source_docs CASCADE")
            print(f"{logprefix()}Truncated source docs table")


async def main():
    global pool
    global pool_size
    pool = await asyncpg.create_pool(
        get_connection_url_pg(),
        min_size=pool_size,
        max_size=pool_size + 3,
    )
    loop = asyncio.get_running_loop()
    loop.set_default_executor(ThreadPoolExecutor(max_workers=20))

    s3_path = sys.argv[1]  # expects s3 url as the argument

    truncate = False
    for arg in sys.argv:
        if arg == "--truncate":
            truncate = True

    if truncate:
        print(f"{logprefix()}WARNING: TRUNCATING TABLES")
        await truncate_tables()

    try:
        bulk_load_json_file = await get_data(s3_path)
        json_file = open(bulk_load_json_file, "r")
        categories = json.load(json_file)
        coroutines = []
        for category in categories:
            folders = (
                "folders"
                if "folders" in category.get("category", {}).keys()
                else "all_folders"
            )
            for folder in category["category"][folders]:
                category_folder_id = await add_category_folder(
                    category["category"]["name"], folder["name"]
                )
                is_first_article = True
                for article in folder["articles"]:
                    if article["status"] == 2:
                        if is_first_article:
                            text = article["title"] + ". " + article["desc_un_html"]
                        else:
                            text = article["desc_un_html"]
                        print(
                            f'{logprefix()}Adding document- {category["category"]["name"]}/{folder["name"]}: {article["title"]}. Len: {len(article["desc_un_html"])}'
                        )
                        coroutines.append(
                            add_document(
                                category["category"]["name"],
                                category_folder_id,
                                article["title"],
                                text,
                                article["description"],
                                article["id"]
                            )
                        )
                        is_first_article = False
                        await asyncio.sleep(0.1)
        await asyncio.gather(*coroutines)
    except Exception as e:
        print(e)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(e)
