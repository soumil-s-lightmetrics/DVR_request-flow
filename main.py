import asyncio
import os
import json
from dotenv import load_dotenv
load_dotenv()

import datetime
import time
import psycopg2.extras as extras
import re
import types
from cachetools import TTLCache
from io import BytesIO

from utils.pg_connections import db_con_pool
from utils.collection_config import categories
from flask import Flask, request, session, jsonify, Response, stream_with_context, send_file
from flask_session import Session
from flask_request_id_header.middleware import RequestID
from assistant_rag import AssistantUtil
import requests
from utils.freshdesk_api_util import FreshdeskAPIUtil
from utils.event_insights_util import fetch_driver_insight
from utils.auth_util import AuthManager
from utils.prompts import generate_fd_ticket_response_system_prompt, generate_fd_ticket_response_user_prompt, intent_classifier_system_prompt, intent_classifier_user_prompt, fleet_lisa_main_system_prompt, lisa_main_system_prompt
import traceback
from logger import websocket_logger, debug_logger, access_logger, log_request
from rag_utils.openai_responses_rag import OpenAIResponsesHandler
from utils.s3_config_manager import S3ConfigManager
from utils.fleet_config_manager import FleetConfigManager
from flask_cors import CORS

from utils.audio_alerts_generator import AudioAlertsGenerator
from utils.tts_config import AUDIO_FORMATS
from utils.tts_validators import validate_tts_request
from rag_utils.pinecone_openai_rag import PineconeOpenAIResponsesHandler


# DVR Request Flow imports
from flask import Flask, send_from_directory, jsonify, request
from flask_sock import Sock
from langgraph.types import Command
from langgraph.errors import GraphInterrupt
from DVR_code.state import drivers_list as DriverModel
from DVR_code.Graph_code import create_graph
from langgraph.checkpoint.sqlite import SqliteSaver
from DVR_code.exceptions import DVRException


def create_app():
    app = Flask(__name__)
    app.secret_key = "jkdfhasdn238423#jbdsj"
    app.config["SESSION_TYPE"] = "filesystem"
    app.config['REQUEST_ID_UNIQUE_VALUE_PREFIX'] = 'CKB-'
    app.permanent_session_lifetime = datetime.timedelta(days=31)
    Session(app)

    return app


if os.getenv("OPENAI_API_KEY") is None or os.getenv("OPENAI_API_KEY") == "":
    raise Exception("Invalid OPENAI_API_KEY in env")

app = create_app()

sock = Sock(app)

RequestID(app)
cors_domains = os.getenv("CORS_DOMAINS")
CORS(app, 
        origins=cors_domains.split(",") if type(cors_domains) is str else [], #Adding CORS allowed domains
        supports_credentials=True)

ws_logger = websocket_logger()
access_logger = access_logger()
debug_logger = debug_logger()
pool = db_con_pool
port = int(os.getenv("PORT", "5000"))
processed_freshdesk_tickets = TTLCache(maxsize=1000, ttl=3600)

global sessionObjectMap
global sessionObjectMap_ext
 
sessionObjectMap = dict()
sessionMap = dict()

# S3 config manager setup
bucket = os.getenv("S3_BUCKET_NAME")
key = os.getenv("S3_CONFIG_KEY")
config_manager = S3ConfigManager(bucket, key)
config_manager.fetch_config()
config_manager.start_periodic_refresh(interval_seconds=3600)

try:
    auth_manager = AuthManager()
except ValueError as e:
    debug_logger.warning(f"AuthManager not initialized: {e}")
    auth_manager = None
audio_alerts_generator = AudioAlertsGenerator(api_key=os.getenv("OPENAI_API_KEY"))
fleet_config_manager = FleetConfigManager(
    ttl_seconds=int(os.getenv("FLEET_CONFIG_CACHE_TTL", "3600")),
    use_dynamic_config=os.getenv("FLEET_CONFIG_USE_DYNAMIC", "true").lower() == "true"
)

def get_session_id():
    sessionId = session.sid
    return sessionId


def initialize_session_objects(sessionId, category):
    assitant_client = AssistantUtil(api_key=os.environ.get("OPENAI_API_KEY"),
                                   assistant_id=os.environ.get("OPENAI_ASSISTANT_ID"),
                                   vecstore_id=os.environ.get("OPENAI_VEC_STORE_ID"))
    try:
        sessionObjectMap[sessionId] = dict()
        sessionObjectMap[sessionId]["conversation_func"] = assitant_client.stream_process_query
    except Exception as e:
        print(e)
        debug_logger.exception("Error initializing session object map.")

def create_session_object(sessionId):
    try:
        openai_response_client = PineconeOpenAIResponsesHandler(
            api_key=os.environ.get("OPENAI_API_KEY"),
            vecstore_id=os.environ.get("OPENAI_VEC_STORE_ID"),
            model=os.environ.get("OPENAI_MODEL"),
            config_manager=config_manager,
            fleet_config_manager=fleet_config_manager,
        )
        sessionMap[sessionId] = {
            "conversation_func": openai_response_client.process_question
        }
    except Exception as e:
        print(e)
        debug_logger.exception("Error initializing session object map.")


# validating the data in metadat field
def check_metadata_sanity(metadata):
    if metadata and isinstance(metadata, dict):
        if len(metadata.keys()) > 10:
            raise ValueError(
                "metadata should be a JSON and it can have only up to 10 keys"
            )
        if any(isinstance(val, dict) for val in metadata.values()):
            raise ValueError("metadata cannot contain nested objects")
        if any(
            (
                not isinstance(val, str)
                and not isinstance(val, int)
                and not isinstance(val, bool)
            )
            for val in metadata.values()
        ):
            raise ValueError("metadata can have string, integer or boolean values only")
        if any(
            (
                False
                if isinstance(val, int)
                or isinstance(val, bool)
                or (len(val) > 0 and len(val) < 100)
                else True
            )
            for val in metadata.values()
        ):
            raise ValueError(
                "metadata can have string values of length not more than 100 characters "
            )
    else:
        raise ValueError("metadata should be a JSON")


def store_chat_history(connection, cursr, session_id, question, answer):
    # skipping a few greetings and acknowledgements. Incorrect responses can be captured via user-feedback
    if isinstance(question, str) and re.sub("[.?|!]", "", question.lower()) in (
        "hi",
        "hello",
        "ok",
        "okay",
        "thank you",
        "thankyou",
        "thanks",
    ):
        return
    insert_query = """
            INSERT INTO chat_history (session_id, question, answer)
            VALUES (%s, %s, %s)
            """
    cursr.execute(insert_query, (session_id, question, answer))
    connection.commit()
    return


@app.before_request
def before_request():
    request.start_time = time.time()

@app.after_request
def after_request(response):
    is_streaming = isinstance(response.response, types.GeneratorType)

    if is_streaming:
        original_gen = response.response
        start_time = getattr(request, 'start_time', time.time())

        def wrapped_gen():
            total_bytes = 0
            first_chunk_sent = False
            ttfb_ms = None
            try:
                for chunk in original_gen:
                    if not first_chunk_sent:
                        ttfb_ms = round((time.time() - start_time) * 1000, 2)
                        first_chunk_sent = True

                    if isinstance(chunk, str):
                       chunk_bytes = chunk.encode('utf-8')
                    else:
                       chunk_bytes = chunk

                    total_bytes += len(chunk_bytes)
                    yield chunk
            finally:
                log_request(response, access_logger, total_bytes, ttfb_ms)

        response.response = stream_with_context(wrapped_gen())
        response.direct_passthrough = False  # Make sure Flask uses the wrapper
        response.headers.pop("Content-Length", None)
        return response

    # Log the request and response
    log_request(response, access_logger)
    return response

@app.route("/health-check", methods=["GET"])
@app.route("/v2/llm-kb/health-check", methods=["GET"])
@app.route("/v1/llm-kb/health-check", methods=["GET"])
def health_check():
    return jsonify({"status": "ok"}), 200


# Gets title of the reference document or the description based on requirement
@app.route("/v1/llm-kb/reference-doc", methods=["POST"])
@app.route("/v2/llm-kb/reference-doc", methods=["POST"])
def fetch_reference_doc():
    data = request.get_json()
    if "docId" not in data or not data["docId"].isnumeric():
        return (
            jsonify({"message": "Invalid input: 'docId' must be a non-empty integer"}),
            400,
        )
    try:
        connection = pool.getconn()
        cursr = connection.cursor()
        if "contentType" in data and data["contentType"] == "title":
            cursr.execute(
                "SELECT TITLE from public.llm_source_docs WHERE ID=%s;", [data["docId"]]
            )
            title = cursr.fetchone()
            return {"title": title[0] if title is not None and len(title) > 0 else ""}
        else:
            cursr.execute(
                "SELECT TITLE, HTML_DATA from public.llm_source_docs WHERE ID=%s;",
                [data["docId"]],
            )
            data = cursr.fetchone()
            return {
                "title": data[0] if data is not None and len(data) > 0 else "",
                "data": data[1] if data is not None and len(data) > 1 else "",
            }
    except Exception as e:
        print(e)
        connection.rollback()
        return (
            jsonify({"message": "an error occured"}),
            500,
        )
    finally:
        cursr.close()
        connection.close()
        pool.putconn(connection)


# handles the user input (streamed or normal based on 'stream' query param)
@app.route("/v2/llm-kb/get-answer-old", methods=["POST"])
@app.route("/v1/llm-kb/get-answer-old", methods=["POST"])
def handle_user_input():
    data = request.get_json()
    stream = request.args.get("stream", "false").lower() == "true"
    include_user_intent = request.args.get("includeUserIntent", "false").lower() == "true"

    if (
        "sessionId" not in data
        or data["sessionId"] is None
        or type(data["sessionId"]) != str
        or data["sessionId"] == ""
    ):
        session["sessionId"] = get_session_id()
        sessionId = session["sessionId"]
    else:
        sessionId = data["sessionId"]

    if sessionId not in sessionObjectMap:
        debug_logger.debug("Initialising SESSION-OBJECT-MAP")
        initialize_session_objects(sessionId, data.get("category", None))

    if (
        "question" not in data
        or data["question"] is None
        or type(data["question"]) != str
        or data["question"] == ""
    ):
        return (
            jsonify(
                {"message": "Invalid input: 'question' must be a non-empty string"}
            ),
            400,
        )
    user_input = data["question"]

    conversation_func = sessionObjectMap[sessionId]["conversation_func"]
    if not callable(conversation_func):
        return (jsonify({"message": "Unknown error"}), 500)

    connection = pool.getconn()
    cursr = connection.cursor()
    try:
        sources = []
        sample_questions = []
        if stream:
            def generator():
                try:
                    for message in conversation_func(sessionId, user_input):
                        event_data = json.loads(message.strip())
                        if event_data.get("event") == "content_block":
                            delta_content = event_data.get("data", {}).get("text", "")
                            yield f"data: {json.dumps({'text': delta_content})}\n\n"

                        if event_data.get("event") == "content_block_references":
                            citations = event_data.get("data", {}).get("references", [])
                            for link in citations:
                                if type(link) is dict:
                                    sources.append({"fd_article": link.get('fd_article_url')})
                            yield f"data: {json.dumps({'sources': sources})}\n\n"

                        if event_data.get("event") == "complete":
                            result_data = event_data.get("data", {})
                            full_text = result_data.get("full_text", "")
                            yield f"data: {json.dumps({'full_text': full_text, 'session_id': sessionId})}\n\n"

                            # Intent classification
                            if include_user_intent:
                                intent = "UNKNOWN"
                                thread_run_args = {
                                    "additional_instructions": intent_classifier_system_prompt,
                                }
                                for message in conversation_func(sessionId, intent_classifier_user_prompt, **thread_run_args):
                                    event_data = json.loads(message.strip())
                                    if event_data.get("event") == "complete":
                                        intent = event_data.get("data", {}).get("full_text", "UNKNOWN")
                                        yield f"data: {json.dumps({'intent': intent})}\n\n"
                                        break
                                
                        if event_data.get("event") == "error":
                            raise ValueError(f"Unable to process request at the moment: {event_data.get('data', {}).get('error', '')}")
                except Exception as e:
                    yield f"event:error\ndata: {json.dumps({'error': str(e)})}\n\n"
                    debug_logger.exception(f"Error in get answer generator function: {str(e)}")

            return Response(
                stream_with_context(generator()),
                mimetype="text/event-stream",
            )
        else:
            full_text = None
            result_data = {}
            
            for message in conversation_func(sessionId, user_input):
                # Parse the message
                event_data = json.loads(message.strip())
                
                # Capture the complete response data
                if event_data.get("event") == "complete":
                    full_text = event_data.get("data", {}).get("full_text")
                    result_data = event_data.get("data", {})
                
                # If there's an error, capture it
                elif event_data.get("event") == "error":
                    raise ValueError(f"Unable to process request at the moment: {event_data.get('data', {}).get('error', '')}")
            result = dict()
            if full_text is not None:
                result['answer'] = full_text
                result["answered"] = "False"
                result['references'] = result_data.get("references")
            if type(result) == str:
                return (jsonify({"answer": result, "sessionId": sessionId}), 200)
            store_chat_history(
                connection, cursr, sessionId, user_input, result["answer"]
            )
            if result.get("references"):
                for link in result["references"]:
                    if type(link) is dict:
                        sources.append({"fd_article": link.get('fd_article_url')})
            
            # Intent classification
            if include_user_intent:
                intent = "UNKNOWN"
                thread_run_args = {
                    "additional_instructions": intent_classifier_system_prompt,
                }
                for message in conversation_func(sessionId, intent_classifier_user_prompt, **thread_run_args):
                    event_data = json.loads(message.strip())
                    if event_data.get("event") == "complete":
                        intent = event_data.get("data", {}).get("full_text", "UNKNOWN")
                        break

            response_data = {
                "answer": result["answer"],
                "sessionId": sessionId,
                "sources": sources,
                "sampleQuestions": sample_questions,
            }
            if include_user_intent:
                response_data["intent"] = intent
            return response_data
    except Exception as e:
        print(e)
        connection.rollback()
        return jsonify({"status": "error", "message": str("somethig went wrong")}), 500
    finally:
        cursr.close()
        connection.close()
        pool.putconn(connection)

# handles the user input (streamed or normal based on 'stream' query param)
@app.route("/v2/llm-kb/get-answer", methods=["POST"])
@app.route("/v1/llm-kb/get-answer", methods=["POST"])
def handle_user_input_new():
    data = request.get_json()
    stream = request.args.get("stream", "false").lower() == "true"
    client_id = request.args.get("clientId", None)
    fleet_id = request.args.get("fleetId", None)
    include_user_intent = request.args.get("includeUserIntent", "false").lower() == "true"

    question = data.get("question", "")
    if not isinstance(question, str) or not question:
        return jsonify({"message": "Invalid input: 'question' must be a non-empty string"}), 400

    if fleet_id and not client_id:
        return jsonify({"message": "Invalid input: 'clientId' is required when 'fleetId' is provided"}), 400

    sessionId = data.get("sessionId") or get_session_id()
    session["sessionId"] = sessionId

    if sessionId not in sessionMap:
        debug_logger.debug("Initialising SESSION-OBJECT-MAP")
        create_session_object(sessionId)

    conversation_func = sessionMap[sessionId].get("conversation_func")
    if not callable(conversation_func):
        return jsonify({"message": "Unknown error"}), 500

    prev_response_id = sessionMap[sessionId].get("prev_response_id")

    def conversation_generator(user_input, sessionId, prev_response_id):
        try:
            instructions = ""
            if client_id and fleet_id:
                fleet_config = fleet_config_manager.get_fleet_config(client_id, fleet_id)
                debug_logger.debug(f"Fleet config for client_id={client_id}, fleet_id={fleet_id}: {fleet_config}")
                instructions = fleet_lisa_main_system_prompt(fleet_config)
            else:
                instructions = lisa_main_system_prompt

            for message in conversation_func(user_input, prev_response_id, instructions, fleet_config=fleet_config if (client_id and fleet_id) else None):
                event_data = json.loads(message.strip())
                event = event_data.get("event")

                if event == "output_text_delta":
                    yield {"type": "text_delta", "data": event_data["data"].get("delta", "")}

                elif event == "content_block_references":
                    yield {"type": "references", "data": event_data["data"].get("references", [])}

                elif event == "complete":
                    yield {"type": "complete", "data": event_data["data"]}

                elif event == "error":
                    yield {"type": "error", "data": event_data["data"]}
        except Exception as e:
            debug_logger.exception(f"Error in generator: {e}")
            yield {"type": "error", "data": {"error": str(e)}}

    def get_user_intent(conversation_func, response_id):
        intent = "UNKNOWN"
        for message in conversation_func(intent_classifier_user_prompt, response_id, intent_classifier_system_prompt):
            event_data = json.loads(message.strip())
            if event_data.get("event") == "complete":
                intent = event_data["data"].get("full_text", "UNKNOWN")
                break
        return intent

    if stream:
        def stream_response():
            sources = []
            for chunk in conversation_generator(question, sessionId, prev_response_id):
                if chunk["type"] == "text_delta":
                    yield f"data: {json.dumps({'text': chunk['data']})}\n\n"

                elif chunk["type"] == "references":
                    sources = [{"fd_article": link.get('fd_article_url')} for link in chunk["data"] if isinstance(link, dict)]
                    yield f"data: {json.dumps({'sources': sources})}\n\n"

                elif chunk["type"] == "complete":
                    full_text = chunk["data"].get("full_text")
                    response_id = chunk["data"].get("response_id")
                    sessionMap[sessionId]["prev_response_id"] = response_id

                    yield f"data: {json.dumps({'full_text': full_text, 'session_id': sessionId})}\n\n"

                    if include_user_intent:
                        intent = get_user_intent(conversation_func, response_id)
                        yield f"data: {json.dumps({'intent': intent})}\n\n"

                    with pool.getconn() as conn:
                        store_chat_history(conn, conn.cursor(), sessionId, question, full_text)

                elif chunk["type"] == "error":
                    yield f"event:error\ndata: {json.dumps({'error': chunk['data'].get('error')})}\n\n"

        return Response(stream_with_context(stream_response()), mimetype="text/event-stream")

    else:
        full_text = None
        sources = []
        response_id = None
        for chunk in conversation_generator(question, sessionId, prev_response_id):
            if chunk["type"] == "text_delta":
                continue
            elif chunk["type"] == "references":
                sources.extend({"fd_article": link.get('fd_article_url')} for link in chunk["data"] if isinstance(link, dict))
            elif chunk["type"] == "complete":
                full_text = chunk["data"].get("full_text")
                response_id = chunk["data"].get("response_id")
                sessionMap[sessionId]["prev_response_id"] = response_id
            elif chunk["type"] == "error":
                return jsonify({"status": "error", "message": chunk["data"].get("error")}), 500

        if not full_text:
            return jsonify({"status": "error", "message": "No response received"}), 500

        with pool.getconn() as conn:
            store_chat_history(conn, conn.cursor(), sessionId, question, full_text)

        result = {
            "answer": full_text,
            "sessionId": sessionId,
            "sources": sources,
            "sampleQuestions": [],
        }

        if include_user_intent:
            result["intent"] = get_user_intent(conversation_func, response_id)

        return jsonify(result)

# Feedback submission
@app.route("/v1/llm-kb/save-feedback", methods=["POST"])
@app.route("/v2/llm-kb/save-feedback", methods=["POST"])
def submit_feedback():
    feedback_data = request.get_json()

    connection = pool.getconn()
    cursr = connection.cursor()

    try:
        query = feedback_data.get("query")
        answer = feedback_data.get("answer")
        feedback_type = feedback_data.get("type")
        comment = feedback_data.get("comment", None)
        metadata = feedback_data.get("metadata", None)
        session_id = feedback_data.get("sessionId")
        timestamp_utc = feedback_data.get("timestampUTC", None)
        submission_timestamp = datetime.datetime.now(datetime.timezone.utc)
        status = feedback_data.get("status", "IN_REVIEW")
        comment_type = feedback_data.get("commentType", "")

        if not query or not answer:
            raise ValueError("Query and answer are required and cannot be empty.")
        valid_feedback_types = ["POSITIVE", "NEGATIVE"]
        if feedback_type not in valid_feedback_types:
            raise ValueError(
                "Feedback type must be one of the following: "
                + ", ".join(valid_feedback_types)
            )
        if session_id is None or not isinstance(session_id, str):
            raise ValueError("Session ID must be a non-empty string.")
        if feedback_type == "NEGATIVE" and comment is not None and (len(str(comment)) == 0 or len(str(comment)) > 3000 ):
            raise ValueError("Comment must be between 1 and 3000 characters.")
        if (
            not timestamp_utc
            or not isinstance(timestamp_utc, str)
            or not time.strptime(timestamp_utc, "%Y-%m-%dT%H:%M:%S.%fZ")
        ):
            raise ValueError("timestampUTC must be passed")

        check_metadata_sanity(metadata)

        if comment_type and type(comment_type) == str:
            comment_type = "_".join(comment_type.lower().split(" "))

        insert_query = """
            INSERT INTO feedback (session_id, question, answer, feedback_type, feedback_comment, timestamp_utc, submission_timestamp, feedback_status, comment_type, metadata)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
        cursr.execute(
            insert_query,
            (
                session_id,
                query,
                answer,
                feedback_type,
                comment,
                timestamp_utc,
                submission_timestamp,
                status,
                comment_type,
                json.dumps(metadata),
            ),
        )
        connection.commit()
        return (
            jsonify({"status": "success", "message": "Thank you for your feedback!"}),
            200,
        )
    except ValueError as e:
        connection.rollback()
        return jsonify({"status": "error", "message": e.args[0]}), 400
    except Exception as e:
        connection.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        cursr.close()
        connection.close()
        pool.putconn(connection)


# Update existing feedback
@app.route("/v1/llm-kb/update-feedback", methods=["POST"])
@app.route("/v2/llm-kb/update-feedback", methods=["POST"])
def update_feedback():
    feedback_data = request.get_json()
    connection = pool.getconn()
    cursr = connection.cursor()
    try:
        feedback_id = feedback_data.get("id")
        query = feedback_data.get("query")
        answer = feedback_data.get("answer")
        feedback_type = feedback_data.get("type")
        comment = feedback_data.get("comment", "").strip()
        metadata = feedback_data.get("metadata", None)
        session_id = feedback_data.get("sessionId")
        review_timestamp = feedback_data.get("reviewTimestamp", None)
        status = feedback_data.get("status", "IN_REVIEW")

        if not feedback_id:
            raise ValueError("id of must be passed to update")
        if not query or not answer:
            raise ValueError("Query and answer are required and cannot be empty.")
        valid_feedback_types = ["POSITIVE", "NEGATIVE"]
        if feedback_type not in valid_feedback_types:
            raise ValueError(
                "Feedback type must be one of the following: "
                + ", ".join(valid_feedback_types)
            )
        if session_id is None or not isinstance(session_id, str):
            raise ValueError("Session ID must be a non-empty string.")
        if len(comment) > 0 and (len(comment) < 10 or len(comment) > 1000):
            raise ValueError("Comment must be between 10 and 500 characters.")
        if (
            not review_timestamp
            or not isinstance(review_timestamp, str)
            or not time.strptime(review_timestamp, "%Y-%m-%dT%H:%M:%S.%fZ")
        ):
            raise ValueError("reviewTimestamp must be passed")

        check_metadata_sanity(metadata)

        update_query = """
            UPDATE feedback 
            SET session_id=%s, question=%s, answer=%s, feedback_type=%s, feedback_comment=%s, review_timestamp=%s, feedback_status=%s, metadata=%s
            WHERE id=%s
            """
        cursr.execute(
            update_query,
            (
                session_id,
                query,
                answer,
                feedback_type,
                comment,
                review_timestamp,
                status,
                json.dumps(metadata),
                feedback_id,
            ),
        )
        connection.commit()
        return (
            jsonify({"status": "success", "message": "Thank you for your feedback!"}),
            200,
        )
    except ValueError as e:
        connection.rollback()
        return jsonify({"status": "error", "message": e.args[0]}), 400
    except Exception as e:
        connection.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        cursr.close()
        connection.close()
        pool.putconn(connection)


# Update existing feedback
@app.route("/v1/llm-kb/feedback-list", methods=["GET"])
@app.route("/v2/llm-kb/feedback-list", methods=["GET"])
def get_feedbacks():
    start_date = request.args.get("startDate")
    end_date = request.args.get("endDate")
    limit = request.args.get(
        "limit", 10
    )  # sending frist 10 records if limit is not passed
    offset = request.args.get("offset", 0)  # skipping 0 records if offset is not passed

    connection = pool.getconn()
    cursr = connection.cursor(cursor_factory=extras.RealDictCursor)
    try:
        if (
            not start_date
            or not isinstance(start_date, str)
            or not time.strptime(start_date, "%Y-%m-%dT%H:%M:%S.%fZ")
        ):
            raise ValueError("startDate must be passed in the correct format")
        if (
            not end_date
            or not isinstance(end_date, str)
            or not time.strptime(end_date, "%Y-%m-%dT%H:%M:%S.%fZ")
        ):
            raise ValueError("endDate must be passed in the correct format")

        fetch_query = """SELECT * FROM feedback WHERE timestamp_utc BETWEEN %s AND %s ORDER BY ID ASC OFFSET %s LIMIT %s"""
        cursr.execute(fetch_query, (start_date, end_date, offset, limit))
        data = cursr.fetchall()
        connection.commit()
        return jsonify(data), 200
    except ValueError as e:
        connection.rollback()
        return jsonify({"status": "error", "message": e.args[0]}), 400
    except Exception as e:
        connection.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        cursr.close()
        connection.close()
        pool.putconn(connection)


# fetch all categories
@app.route("/v1/llm-kb/all-categories", methods=["GET"])
@app.route("/v2/llm-kb/all-categories", methods=["GET"])
def get_all_categories():
    return jsonify(categories), 200


# fetch questions for category
@app.route("/v1/llm-kb/category-questions", methods=["GET"])
@app.route("/v2/llm-kb/category-questions", methods=["GET"])
def get_category_questions():
    category = request.args.get("category")
    connection = pool.getconn()
    cursr = connection.cursor(cursor_factory=extras.RealDictCursor)

    try:
        if not category or not isinstance(category, str):
            raise ValueError("category must be passed")
        if category not in categories.keys():
            raise ValueError("incorrect category name passed")

        fetch_query = """SELECT question FROM category_questions WHERE category=%s"""
        cursr.execute(fetch_query, [category])
        data = cursr.fetchall()
        connection.commit()
        questions = []
        for item in data:
            questions.append(item["question"])
        return jsonify(questions), 200
    except ValueError as e:
        connection.rollback()
        return jsonify({"status": "error", "message": e.args[0]}), 400
    except Exception as e:
        connection.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        cursr.close()
        connection.close()
        pool.putconn(connection)

@app.route("/v1/llm-kb/generate-fd-ticket-response/<int:ticket_id>", methods=["POST"])
@app.route("/v2/llm-kb/generate-fd-ticket-response/<int:ticket_id>", methods=["POST"])
def generate_freshdesk_ticket_response(ticket_id):
    if ticket_id in processed_freshdesk_tickets:
        return jsonify({"message": "Ticket already processed recently"}), 200

    try:
        # Initialize Freshdesk API util and fetch ticket details
        freshdesk_api_util = FreshdeskAPIUtil()
        ticket_data = asyncio.run(freshdesk_api_util.get_ticket_details(ticket_id))
        if not ticket_data:
            return jsonify({"message": "Ticket not found"}), 404

        user_query = generate_fd_ticket_response_user_prompt(ticket_data)
        system_message = generate_fd_ticket_response_system_prompt

        openai_response_client = OpenAIResponsesHandler(
            api_key=os.environ.get("OPENAI_API_KEY"),
            vecstore_id=os.environ.get("OPENAI_VEC_STORE_ID"),
            model=os.environ.get("OPENAI_MODEL"),
            config_manager=config_manager
        )

        # Process LLM response
        full_text, result_data = None, {}
        context_args = {
            "tool_choice": { "type": "file_search" }
        }
        for message in openai_response_client.process_question(
            user_query, None, system_message, **context_args
        ):
            event_data = json.loads(message.strip())
            if event_data.get("event") == "complete":
                full_text = event_data.get("data", {}).get("full_text")
                result_data = event_data.get("data", {})
            elif event_data.get("event") == "error":
                raise ValueError("Unable to process request at the moment")

        if not full_text:
            return jsonify({"message": "No answer generated"}), 500

        # Format references if present
        answer = full_text.replace('\n', '')
        references = result_data.get("references")
        if references and isinstance(references, list):
            # Ensure unique references based on 'id'
            references = list({r['id']: r for r in references if isinstance(r, dict) and 'id' in r}.values())
            links = [
                f'<li><a href="{link.get("fd_article_url")}">{link.get("fd_article_url")}</a></li>'
                for link in references if isinstance(link, dict) and link.get('fd_article_url')
            ]
            if links:
                answer += f'<p><strong>References:</strong></p><ul>{"".join(links)}</ul>'

        # Add note to Freshdesk ticket
        add_note_response = asyncio.run(
            freshdesk_api_util.create_reply_to_ticket(ticket_id, answer)
        )
        processed_freshdesk_tickets[ticket_id] = True
        return jsonify(add_note_response), 200

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/v1/llm-kb/driver-insight", methods=["POST"])
@app.route("/v2/llm-kb/driver-insight", methods=["POST"])
def driver_insight():
    data = request.get_json()
    if not data:
        return jsonify({"status": "error", "message": "Request body is required"}), 400

    driver_id = data.get("driverId")
    fleet_id  = data.get("fleetId")
    tsp_id    = data.get("tspId")
    units     = data.get("units", "km")
    if units not in ("km", "mi"):
        return jsonify({"status": "error", "message": "'units' must be 'km' or 'mi'"}), 400
    if not driver_id or not fleet_id or not tsp_id:
        return jsonify({"status": "error", "message": "'driverId', 'fleetId' and 'tspId' are required"}), 400

    try:
        insight = fetch_driver_insight(fleet_id, driver_id, tsp_id, units, auth_manager, debug_logger)
        return jsonify({"driverId": driver_id, "fleetId": fleet_id, "insight": insight}), 200
    except ValueError as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    except Exception as e:
        debug_logger.exception(f"Error in driver_insight: {e}")
        return jsonify({"status": "error", "message": "Failed to generate insight"}), 500


@app.route("/v1/llm-kb/test-streaming", methods=["GET"])
@app.route("/v2/llm-kb/test-streaming", methods=["GET"])
def streaming_test():
    def generate():
        for i in range(5):
            yield f"data: {json.dumps({'text': f'This is message {i}'})}\n\n"
            time.sleep(1)

    return Response(stream_with_context(generate()), mimetype='text/event-stream')

@app.route("/v1/llm-kb/audio-alerts/tts", methods=["POST"])
@app.route("/v2/llm-kb/audio-alerts/tts", methods=["POST"])
def text_to_speech():
    """
    Convert text to speech using OpenAI TTS API.

    Accepts either:
    - Modern approach: gender + tone parameters
    - Legacy approach: direct voice name
    """
    data = request.get_json()

    # Validate all parameters
    is_valid, error_message, validated_params = validate_tts_request(data)
    if not is_valid:
        return jsonify({"message": error_message}), 400

    try:
        # Call TTS with validated parameters
        tts_response = audio_alerts_generator.convert_text_to_speech(
            text=validated_params["text"],
            voice=validated_params["voice"],
            format=validated_params["format"],
            speed=validated_params["speed"],
            instructions=validated_params.get("instructions")
        )
        tts_audio_bytes = tts_response.read()

        # Prepare in-memory audio buffer
        audio_buffer = BytesIO()
        audio_buffer.write(tts_audio_bytes)
        audio_buffer.seek(0)

        # Return audio file
        return send_file(
            audio_buffer,
            mimetype=AUDIO_FORMATS[validated_params["format"]],
            as_attachment=False,
            download_name=validated_params["filename"]
        )

    except Exception as e:
        # Log detailed error for debugging
        debug_logger.exception(f"TTS conversion error: {str(e)}")

        # Return generic error to client
        return jsonify({
            "message": "Error processing TTS request",
            "error": str(e)
        }), 500
    
@app.errorhandler(500)
def error_500(e):
    error_details = traceback.format_exc()
    log_data = {
        'requestId': request.environ.get("HTTP_X_REQUEST_ID")
    }
    debug_logger.debug(f"Error occurred at {request.path} | Status: 500 | Traceback: {error_details}", extra=log_data)
    return jsonify(message=str(e)), 500


@app.errorhandler(415)
def error_415(e):
    error_details = traceback.format_exc()
    log_data = {
        'requestId': request.environ.get("HTTP_X_REQUEST_ID")
    }
    debug_logger.debug(f"Error occurred at {request.path} | Status: 415    | Traceback: {error_details}", extra=log_data)
    return jsonify(message=str(e)), 415


## DVR Routes
@app.after_request
def log_http_request(response):
    """Automatically pipes every completed HTTP endpoint transaction into access.log."""
    log_request(response, access_logger)
    return response


# Built React app (frontend/dist). Falls back to the legacy single-file
# DVR_frontend.html when the bundle hasn't been built yet (`cd frontend && npm run build`).
FRONTEND_DIST = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'frontend', 'dist')


@app.route('/')
def get_ui():
    if os.path.exists(os.path.join(FRONTEND_DIST, 'index.html')):
        return send_from_directory(FRONTEND_DIST, 'index.html')
    return send_from_directory('.', 'DVR_frontend.html')


@app.route('/assets/<path:filename>')
def frontend_assets(filename):
    """Serve Vite's hashed JS/CSS bundles referenced by the built index.html."""
    return send_from_directory(os.path.join(FRONTEND_DIST, 'assets'), filename)


@app.route('/<fleet_id>/load-data')
def fetch_driver_data(fleet_id):
    # Retrieve fresh auth tokens via management layers
    from utils.auth import get_headers

    drivers_data = auth_manager.make_api_request(
        client_id=os.getenv('CLIENT_ID'),
        endpoint=f'/v2/fleets/{fleet_id}/drivers/list',
    )

    drivers = drivers_data.get('rows', [])
    driver_list = [
        {
            'driverId': d.get('driverId') or 'UNASSIGNED',
            'driverName': d.get('driverName') or 'UNASSIGNED'
        }
        for d in drivers
    ]

    fleet_response = auth_manager.make_api_request(
        client_id=os.getenv("CLIENT_ID"),
        endpoint=f'/v2/fleets/{fleet_id}/trips'
    )

    fleet_trips = fleet_response.get('rows', [])

    asset_set = set()
    trip_ids = []
    fleet_events = set()
    for trip in fleet_trips:
        asset_set.add(trip['asset']['assetId'])
        trip_ids.append(trip['tripId'])
        for key in trip.get('eventCount', {}):
            fleet_events.add(key.lower().strip())

    return jsonify({
        'drivers': driver_list,
        'events': list(fleet_events),
        'asset_ids': list(asset_set),
        'trip_ids': trip_ids
    })


@app.route('/health')
def health():
    return jsonify({'status': 'running'})


def _invoke_graph(ws, graph, state_or_command, config):
    """
    Single wrapper for all graph.invoke() calls.
    Handles DVRException, GraphInterrupt, and generic exceptions
    in one place so we don't repeat the try/except three times.
    """
    try:
        result = graph.invoke(state_or_command, config=config)
        _handle_result(ws, result)

    except DVRException as e:
        ws_logger.error(f'DVR error [{e.status_code}]: {e.message}')
        ws.send(json.dumps(e.to_dict()))

    except GraphInterrupt:
        ws_logger.error(
            "GraphInterrupt leaked out of graph.invoke(). "
            "Check that your checkpointer is initialised correctly."
        )
        ws.send(json.dumps({
            "type": "error",
            "message": "Something interrupted the process unexpectedly. Please try again."
        }))

    except Exception as e:
        ws_logger.error(f'Unexpected error: {e}', exc_info=True)
        ws.send(json.dumps({
            "type": "error",
            "message": "Something went wrong on our end. Please try again."
        }))


@sock.route('/chat')
def chat_socket(ws):
    fleet_data = {
        "drivers": [],
        "asset_ids": [],
        "trip_ids": [],
        "events": [],
        "fleet_id": None
    }

    ws_logger.info("Client connected")
    graph = create_graph()
    try:
        while True:
            raw = ws.receive()
            if raw is None:
                ws_logger.info("Client sent empty frame — closing.")
                break

            try:
                payload = json.loads(raw)
            except json.JSONDecodeError as e:
                ws_logger.warning(f"Malformed JSON from client: {e}")
                ws.send(json.dumps({
                    "type": "error",
                    "message": "Invalid JSON payload."
                }))
                continue

            msg_type = payload.get("type")
            thread_id = payload.get("thread_id")
            config = {"configurable": {"thread_id": thread_id}}
            ws_logger.info(f"ws message: {msg_type} | thread: {thread_id}")

            try:
                if msg_type == "load_data":
                    raw_fleet = payload.get("fleet_data", {})
                    fleet_data = {
                        "fleet_id":   raw_fleet.get("fleet_id"),
                        "drivers":    raw_fleet.get("drivers", []),
                        "asset_ids":  raw_fleet.get("asset_ids", []),
                        "trip_ids":   raw_fleet.get("trip_ids", []),
                        "events":     raw_fleet.get("events", []),
                        "driver_objects": [
                            DriverModel(
                                driverId=d["driverId"],
                                driverName=d["driverName"]
                            )
                            for d in raw_fleet.get("drivers", [])
                        ]
                    }
                    ws_logger.info(
                        f"Fleet loaded: {fleet_data['fleet_id']} | "
                        f"{len(fleet_data['drivers'])} drivers | "
                        f"{len(fleet_data['asset_ids'])} assets"
                    )
                    ws.send(json.dumps({"type": "load_complete"}))

                elif msg_type == "autocomplete":
                    option = payload.get("option")
                    search = payload.get("search", "").lower()
                    suggestions = []

                    if option == "Drivers":
                        suggestions = [
                            d for d in fleet_data["drivers"]
                            if d["driverName"].lower().startswith(search)
                        ]
                    elif option == "Assets":
                        suggestions = [
                            a for a in fleet_data["asset_ids"]
                            if a.lower().startswith(search)
                        ]
                    elif option == "Trips":
                        suggestions = [
                            t for t in fleet_data["trip_ids"]
                            if t.lower().startswith(search)
                        ]
                    elif option == "Event Types":
                        suggestions = [
                            e for e in fleet_data["events"]
                            if e.lower().startswith(search)
                        ]
                    else:
                        ws_logger.warning(f"Unknown autocomplete option: {option}")

                    ws.send(json.dumps({
                        "type": "autocomplete_results",
                        "suggestions": suggestions[:10]
                    }))

                elif msg_type == "autocomplete_result":
                    option = payload.get("option")
                    item = payload.get("selectedItem")

                    if not item:
                        ws.send(json.dumps({
                            "type": "error",
                            "message": "No item selected."
                        }))
                        continue

                    state = {
                        "query":      payload.get("query", ""),
                        "fleet_id":   fleet_data.get("fleet_id"),
                        "drivers":    fleet_data.get("driver_objects", []),
                        "query_type": "directed"
                    }

                    if option == "Drivers":
                        state["chosen_driver"] = [{
                            "driverId":   item["driverId"],
                            "driverName": item["driverName"]
                        }]
                    elif option == "Assets":
                        state["chosen_asset_id"] = (
                            [item] if isinstance(item, str)
                            else [item.get("assetId")]
                        )
                    elif option == "Trips":
                        state["chosen_trip_id"] = (
                            item if isinstance(item, str)
                            else item.get("tripId")
                        )
                    elif option == "Event Types":
                        state["chosen_event"] = (
                            [item] if isinstance(item, str)
                            else [item.get("event_type")]
                        )
                    else:
                        ws_logger.warning(
                            f"Unknown autocomplete_result option: {option}"
                        )

                    # uses the wrapper — no try/except needed here
                    _invoke_graph(ws, graph, state, config)

                elif msg_type == "resume_graph":
                    resume_value = payload.get("resume_value")
                    if resume_value is None:
                        ws.send(json.dumps({
                            "type": "error",
                            "message": "Missing resume_value."
                        }))
                        continue

                    _invoke_graph(ws, graph, Command(resume=resume_value), config)

                elif msg_type == "only_query":
                    query = payload.get("query", "").strip()
                    if not query:
                        ws.send(json.dumps({
                            "type": "error",
                            "message": "Empty query."
                        }))
                        continue

                    state = {
                        "query":      query,
                        "fleet_id":   fleet_data.get("fleet_id"),
                        "drivers":    fleet_data.get("driver_objects", []),
                        "query_type": "simple_query"
                    }

                    _invoke_graph(ws, graph, state, config)

                else:
                    ws_logger.warning(f"Unknown message type: {msg_type}")
                    ws.send(json.dumps({
                        "type": "error",
                        "message": f"Unknown message type: {msg_type}"
                    }))

            except Exception as e:
                ws_logger.error(
                    f"Error handling '{msg_type}' message: {e}",
                    exc_info=True
                )
                ws.send(json.dumps({
                    "type": "error",
                    "message": "Something went wrong. Please try again."
                }))

    except Exception as e:
        ws_logger.error(f"WebSocket connection error: {e}", exc_info=True)

    finally:
        ws_logger.info("Client disconnected.")


def _handle_result(ws, result: dict):
    """Send graph result back to the frontend over the WebSocket."""
    ws_logger.info(f"graph result keys: {list(result.keys()) if isinstance(result, dict) else type(result)}")
    ws_logger.info(f"graph result (full): {result}")
    if not isinstance(result, dict):
        ws_logger.error(f"Unexpected graph result type: {type(result)}")
        ws.send(json.dumps({
            "type": "error",
            "message": "Unexpected response from graph."
        }))
        return

    if "__interrupt__" in result:
        # Send any partial chat response before showing the interrupt UI
        if result.get("chat_response"):
            ws.send(json.dumps({
                "type": "chat_response",
                "response": {"chat_response": result["chat_response"]},
                "more": True
            }))

        interrupt_data = result["__interrupt__"][0].value
        ws.send(json.dumps({
            "type": "interrupt",
            "payload": interrupt_data
        }))
        return

    response = {}

    if result.get("uploadRequestId"):
        response["uploadRequestId"] = result["uploadRequestId"]
        response["dvr_summary"] = result.get("dvr_summary")
    elif result.get("chat_response"):
        response["chat_response"] = result["chat_response"]
    else:
        response["chat_response"] = "Request processed."

    ws.send(json.dumps({
        "type": "chat_response",
        "response": response,
        "more": False
    }))


# if __name__ == "__main__":
#     app.run(host="0.0.0.0", debug=os.getenv("FLASK_DEBUG", "false").lower() == "true", port=port)