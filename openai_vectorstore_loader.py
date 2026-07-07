import os
import asyncio
import datetime
import re
from openai import OpenAI
import io
import requests
import json

# Initialize the OpenAI client
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
previous_millis = datetime.datetime.now().timestamp()


def logtimedelta():
    global previous_millis
    current_millis = datetime.datetime.now().timestamp()
    val = round(current_millis - previous_millis, 3)
    previous_millis = current_millis
    return val

def logprefix():
    return f"{datetime.datetime.now().isoformat()}({logtimedelta()}) "

async def add_document(item):
    # async operation
    print(
        f'{logprefix()}Adding document- {item["title"]}'
    )

    # # Create a file buffer with HTML content
    html_content = f"""<!DOCTYPE html>
    <html>
    <head>
        <title>{item["title"]}</title>
    </head>
    <body>
        {item["content"]}
    </body>
    </html>"""

    # Convert string to bytes and create file buffer
    file_buffer = io.BytesIO(html_content.encode('utf-8'))
    file_buffer.name = re.sub('[^A-Za-z0-9]+', '_', item['title']) + '.html' # Giving the buffer a filename

    metadata = {
        "fd_article_url": "https://lightmetrics.freshdesk.com/a/solutions/articles/" + str(item["article_id"]),
        "id": item["article_id"]
    }

    # Create a file object with the OpenAI API using the buffer
    file_response = client.files.create(
        file=file_buffer,
        purpose="assistants"
    )

    file_id = file_response.id

    # Add the file to the vector store with metadata
    file_batch = client.beta.vector_stores.file_batches.create(
        vector_store_id="vs_6810697d60048191b1e94f1994365a53",
        file_ids=[file_id]
    )

    url = f"https://api.openai.com/v1/vector_stores/vs_6810697d60048191b1e94f1994365a53/files/{file_id}"

    payload = json.dumps({
        "attributes": metadata
    })
    headers = {
        'Authorization': f'Bearer {os.environ.get("OPENAI_API_KEY")}',
        'Content-Type': 'application/json'
    }

    try:
        response = requests.request("POST", url, headers=headers, data=payload)
        print(
            f'{logprefix()}updated metadata for document- {item["title"]}'
        )
    except requests.exceptions.RequestException as e:  # This is the correct syntax
        print(e)
    

async def process_data(doc_list):
    # Create a list to store all the tasks for add_document calls
    tasks = []
    for item in doc_list:
        task = asyncio.create_task(add_document(item))
        tasks.append(task)
    
    # Wait for all tasks to complete
    await asyncio.gather(*tasks)

async def parse_files():
    try:
        # bulk_load_json_file = await get_data(s3_path)
        json_file = open('data/Solutions.json', "r")
        categories = json.load(json_file)
        data_list = []
        for category in categories:
            if category["category"]["name"] == "Lisa Chat Bot":
                folders = (
                    "folders"
                    if "folders" in category.get("category", {}).keys()
                    else "all_folders"
                )
                for folder in category["category"][folders]:
                    for article in folder["articles"]:
                        data_list.append(dict(
                            title=article['title'],
                            content=article["description"],
                            category=folder["name"],
                            article_id=article["id"]
                        ))
            else:
                folders = (
                    "folders"
                    if "folders" in category.get("category", {}).keys()
                    else "all_folders"
                )
                for folder in category["category"][folders]:
                    for article in folder["articles"]:
                        if article["status"] == 2 and category["category"]["name"] in (
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
                                    ):
                            data_list.append(dict(
                                title=article['title'],
                                content=article["description"],
                                category=category["category"]["name"],
                                article_id=article["id"]
                            ))
        await process_data(data_list)
    except Exception as e:
        print(e)

asyncio.run(parse_files())