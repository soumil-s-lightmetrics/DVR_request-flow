from dotenv import load_dotenv
load_dotenv()

import os
import asyncio
from openai import OpenAI

# Validate required environment variables
REQUIRED_ENV_VARS = [
    "OPENAI_API_KEY",
    "OPENAI_VEC_STORE_ID",
]

missing_vars = [var for var in REQUIRED_ENV_VARS if not os.getenv(var)]
if missing_vars:
    raise EnvironmentError(f"Missing required environment variables: {', '.join(missing_vars)}")

# Initialize the OpenAI client
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
vector_store_id = os.environ.get("OPENAI_VEC_STORE_ID")

async def get_all_vectorstore_files():
    files = []
    list_params = {
        "vector_store_id": vector_store_id,
        "limit": 100,
        "filter": "failed"
    }
    response = client.vector_stores.files.list(**list_params)
    files.extend(response.data)

    while response.has_next_page():
        response = client.vector_stores.files.list(
            **list_params,
            **response.next_page_info().params,
        )
        files.extend(response.data)

    return files

async def main():
    print("--START--")

    print("Fetching files in vector store with 'failed' status:")
    files = await get_all_vectorstore_files()
    print(f"Total files with 'failed' status: {len(files)}")

    for file in files:
        print(f"File ID: {file.id}, Status: {file.status}, Created At: {file.created_at}")
        client.vector_stores.files.delete(
            vector_store_id=vector_store_id, file_id=file.id
        )
        print(f"Deleted file ID: {file.id}")

        client.vector_stores.files.create(
            vector_store_id=vector_store_id,
            file_id=file.id,
            attributes=file.attributes
        )
        print(f"Recreated file ID: {file.id}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(e)