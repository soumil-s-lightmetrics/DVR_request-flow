import logging
from logging.handlers import RotatingFileHandler
import os

os.makedirs("logs", exist_ok=True)


def configure_logger(filename, logger_name):
    logformatter = logging.Formatter(
        "[%(asctime)s] {%(filename)s:%(lineno)d}%(levelname)s %(name)s - %(message)s"
    )
    logHandler = RotatingFileHandler(
        filename,
        mode="a",
        maxBytes=5 * 1024 * 1024,
        backupCount=2,
        encoding=None,
        delay=0,
    )
    logHandler.setFormatter(logformatter)

    logger = logging.getLogger(logger_name)
    logger.addHandler(logHandler)
    logger.setLevel(logging.DEBUG)

    if (
        os.environ.get("LOG_TO_STDOUT") != None
        and os.environ.get("LOG_TO_STDOUT").lower() == "true"
    ):
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(logformatter)
        logger.addHandler(stream_handler)

    return logger


# LOG - main.py
def main_logger():
    logger_main = configure_logger("logs/main.log", "MAIN_LOG")
    return logger_main


# #LOG - app.py
def app_logger():
    logger_app = configure_logger("logs/app.log", "APP_LOG")
    return logger_app


# #LOG - source_attrib.py
def data_processing_logger():
    logger_data_processing = configure_logger(
        "logs/data_processing.log", "Data_Processing"
    )
    return logger_data_processing


# LOG - view_docs.py
def view_docs_logger():
    logger_view_docs = configure_logger("logs/view_docs.log", "View_Docs")
    return logger_view_docs


# LOG - add_doc.py
def add_doc_logger():
    logger_add_doc = configure_logger("logs/add_doc.log", "Add Doc")
    return logger_add_doc


# #LOG - edit_doc.py
def edit_doc_logger():
    logger_edit_doc = configure_logger("logs/edit_doc.log", "Edit Doc")
    return logger_edit_doc


# LOG - connect_db.py
def connect_db_logger():
    logger_connect_db = configure_logger("logs/connect_db.log", "Connect_DB")
    return logger_connect_db


if __name__ == "__main__":
    pass
