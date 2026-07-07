import io
import datetime
import json
import asyncio
import boto3
import zipfile
import re
import os
from urllib.parse import urlparse
from llama_cloud.client import LlamaCloud


previous_millis = datetime.datetime.now().timestamp()

client = LlamaCloud(token='llx-CB9wxIm91OFDVcwwmddyfzp53OIGoJMxCWh0LucJpwdqQm0W')

categorised_index = '09666681-33da-4984-9edd-1c76092b7b48'

internal_index = '96bc6a60-7d07-4131-9380-528ad15918e4'

general_indices = ['8f0bec14-595a-4bc6-9635-374c296e4751',
                   '0910ecf1-5ec6-4890-92c2-a5fb3d310c8f',
                   '7ee67ed1-c84c-4221-a80d-b3afe68253e3', 
                   'ab0a3afa-bece-40d7-8f05-94f4c421c2a5',
                   '549bca99-039d-413f-a6e7-a464c1ec67c6',
                   'c7919cfd-50a0-411c-b452-9460aac48c7b']


def logtimedelta():
    global previous_millis
    current_millis = datetime.datetime.now().timestamp()
    val = round(current_millis - previous_millis, 3)
    previous_millis = current_millis
    return val


def logprefix():
    return f"{datetime.datetime.now().isoformat()}({logtimedelta()}) "


async def get_data(path):
    url_obj = urlparse(path, allow_fragments=False)

    s3 = boto3.client("s3")
    s3.download_file(url_obj.netloc, url_obj.path.strip("/"), "data.zip")

    with zipfile.ZipFile("./data.zip", "r") as zip_ref:
        zip_ref.extractall("./data")
    return "./data/Solutions.json"

async def process_data(data_list):
    # Initialize 6 smaller lists for 'general' values
    general_lists = [[] for _ in range(6)]
    
    # Create a list to store all the tasks for add_document calls
    tasks = []
    
    # Process each item in the data list
    for item in data_list:
        if item['category'] not in ('general', 'internal'):
            # Create and add task for add_document
            item['index_id'] = categorised_index
            task = asyncio.create_task(add_document(item))
            tasks.append(task)
        elif item['category'] == 'internal':
            item['index_id'] = internal_index
            # Create and add task for add_document
            task = asyncio.create_task(add_document(item))
            tasks.append(task)
        elif item['category'] == 'general':
            # Add to the appropriate list (synchronously)
            add_to_general_list(item, general_lists)
    
    # Wait for all tasks to complete
    await asyncio.gather(*tasks)
    
    # Now process each element in the general lists with function_c
    general_tasks = []
    shard = 0
    for lst in general_lists:
        ref = general_indices[shard]
        for item in lst:
            item['index_id'] = ref
            task = asyncio.create_task(add_document(item))
            general_tasks.append(task)
        shard = shard + 1
    
    # Wait for all function_c tasks to complete
    await asyncio.gather(*general_tasks)
    
    return general_lists

def add_to_general_list(item, general_lists):
    # This is a synchronous function to add items to the general lists
    # Find the first list that isn't at max capacity
    for i in range(6):
        if len(general_lists[i]) < 250:
            general_lists[i].append(item)
            break

# Example async function definitions
async def add_document(item):
    # Simulate async operation
    print(
        f'{logprefix()}Adding document- {item["title"]}'
    )
    try:
        title = re.sub('[^A-Za-z0-9]+', '_', item['title']) + '.html'
        category_key = "_".join(item['category'].lower().split(" "))
        fd_article = "https://lightmetrics.freshdesk.com/a/solutions/articles/"+ str(item['article_id'])
        content_file = io.BytesIO(item['content'].encode('utf-8'))
        content_file.name = title
        
        print(category_key, title, fd_article, item['index_id'])
        file = client.files.upload_file(upload_file=content_file, project_id="54027e00-faeb-43c2-bdf7-76c7b4198c6a")
        pipeline_files = client.pipelines.add_files_to_pipeline_api(pipeline_id=item['index_id'], request=[{'file_id': file.id}])
        custom_meta = client.pipelines.update_pipeline_file(file_id=file.id, pipeline_id=item['index_id'],  custom_metadata={
                'category': category_key,
                'fd_article': fd_article
            })
    except Exception as e:
        print(e)
    await asyncio.sleep(0.1)  # Replace with actual async operation

async def parse_files(s3_path):
    try:
        bulk_load_json_file = await get_data(s3_path)
        json_file = open(bulk_load_json_file, "r")
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
                        if article["status"] == 2:
                            data_list.append(dict(
                                title=article['title'],
                                content=article["description"],
                                category='internal' if category["category"]["name"] not in (
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
                                    ) else 'general',
                                article_id=article["id"]
                            ))
        await process_data(data_list)
    except Exception as e:
        print(e)

asyncio.run(parse_files('s3://lm-dev-infra-artefacts-ap-south-1/api/eks/llm-kb/freshdesk-solutions/2025-02-10.zip'))
