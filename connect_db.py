from dotenv import load_dotenv

load_dotenv()

import xml.etree.ElementTree as ET
from langchain.document_loaders import DirectoryLoader
from config_log import connect_db_logger
from utils.pg_connections import get_new_connection

logger_connect_db = connect_db_logger()

tree = ET.parse("org_data/Solutions.xml")
root = tree.getroot()
count_questions = 1
path = "solution_category/"

try:
    conn = get_new_connection()
    logger_connect_db.debug("Database connected successfully")

    cur = conn.cursor()

    cur.execute("CREATE EXTENSION IF NOT EXISTS vector")

    cur.execute(
        """
    CREATE TABLE IF NOT EXISTS public.llm_category_folder(
                ID SERIAL PRIMARY KEY,
                CATEGORY TEXT NOT NULL,
                FOLDER TEXT,
                CONSTRAINT U_CATEGORY_FOLDER UNIQUE (CATEGORY,FOLDER)
    )
    """
    )

    cur.execute(
        """
    CREATE TABLE IF NOT EXISTS public.llm_source_docs(
                ID SERIAL PRIMARY KEY,
                TITLE TEXT,
                CATEGORY_FOLDER_ID INT,
                TEXT_DATA TEXT,
                CONSTRAINT FK_SOURCE_CATEGORY FOREIGN KEY(CATEGORY_FOLDER_ID) 
                REFERENCES public.llm_category_folder(ID)
    )
    """
    )

    for solution_category in root.findall("solution-category"):
        category_name = solution_category.find("name").text
        logger_connect_db.debug(category_name)

        for solution_folder in solution_category.findall(".//solution-folder"):
            sub_category_name = solution_folder.find("name").text
            logger_connect_db.debug(sub_category_name)

            for solution_article in solution_folder.findall(".//solution-article"):
                question = solution_article.find("title").text
                answer = solution_article.find("desc-un-html").text

                postgres_insert_query_category_folder = """INSERT INTO public.llm_category_folder(CATEGORY, FOLDER) VALUES (%s,%s) ON CONFLICT ON CONSTRAINT U_CATEGORY_FOLDER DO UPDATE SET ID=public.llm_category_folder.id RETURNING ID as CATEGORY_ID"""
                record_to_insert = [category_name, sub_category_name]
                cur.execute(postgres_insert_query_category_folder, record_to_insert)

                category_folder_id = cur.fetchone()[0]
                logger_connect_db.debug(f"Categry_folder_id={category_folder_id}")

                postgres_insert_query_source_doc = """INSERT INTO public.llm_source_docs (TITLE, CATEGORY_FOLDER_ID, TEXT_DATA) VALUES (%s,%s,%s)"""
                record_to_insert = [
                    question,
                    category_folder_id,
                    question + ".\n" + answer,
                ]
                cur.execute(postgres_insert_query_source_doc, record_to_insert)

                conn.commit()

                # dir_path = f"{path}{category_name}/{sub_category_name}"
                # os.makedirs(dir_path, exist_ok=True)
                # with open(f"{dir_path}/file_lm{category_folder_id}.txt","a") as file:
                #     file.write(question+".\n")
                #     file.write(answer)
                #     file.close()
                # count_questions += 1

    conn.commit()

except Exception as e:
    logger_connect_db.exception("Error: Database not connected")

finally:
    cur.close()
    conn.close()
