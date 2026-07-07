import os
from dotenv import load_dotenv

load_dotenv()

from utils.vectorstore import vectorstores
from utils.collection_config import collections
from langchain_openai import ChatOpenAI
from langchain_openai import OpenAIEmbeddings
from langchain.retrievers import ContextualCompressionRetriever
from langchain_core.prompts import ChatPromptTemplate
from langchain.chains import create_history_aware_retriever, create_retrieval_chain
from langchain.chains.combine_documents import create_stuff_documents_chain
from langchain_core.runnables.history import RunnableWithMessageHistory
from langchain_core.chat_history import BaseChatMessageHistory
from langchain_core.messages import BaseMessage
from langchain_community.document_transformers import EmbeddingsRedundantFilter
from langchain.retrievers.document_compressors import DocumentCompressorPipeline
from langchain.retrievers import ContextualCompressionRetriever
from langchain_community.document_transformers import LongContextReorder
from langchain.retrievers.document_compressors import EmbeddingsFilter
from langchain.retrievers import EnsembleRetriever
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnableBranch, RunnableLambda, RunnablePassthrough, RunnableParallel
from utils.pg_connections import db_con_pool
import psycopg2.extras as extras

pool = db_con_pool

from pydantic import BaseModel, Field
from typing import List

llm = ChatOpenAI(model="gpt-4o-mini")

system_prompt = (
   "You are an AI assistant designed to answer questions using the provided context. Your response should strictly adhere to the context and be clear, concise, and structured. Please follow these guidelines:"
    "1. **Relevance**: Use only the provided context to answer the question. Do not answer outside the context even if you know the answer. If the context lacks sufficient information, respond: *`Unfortunately, I am unable to answer that question.`*"
    "2. **Clarity and Completeness**:"
    "- Provide complete answers where possible, breaking down complex responses into bullet points or numbered steps for readability."
    "3. **Examples and Specificity**:"
    "- Provide specific details or examples from the context."
    "- If examples aren't available, avoid making them up."
    "4. **Alternative Suggestions**:"
    "- If the answer isn't fully available, suggest actions: `Refer to the platform's documentation` or `Contact support.`"
    "5. **Error Handling**:"
    "- If the question is unclear or lacks context, state: `Could you please provide more details?`"
    "6. **Structured Greetings**:"
    "- Respond politely to greetings and acknowledgments."
    "- Limit answers to under 200 characters unless detailed explanation is required."
    "7. **Context Consistency**:"
    "- Make sure the context is comprehensive, formatted clearly, and relevant to the question."

    "Unless absolutely neccesary and it is relevant to the question, always limit the characters in the final answer to under 200 characters"
    "Dont justify your answers. Dont give information not mentioned in the context"
    "\n question: {input}"
    "\n context: {context}"
)

condense_question_system_template = (
    "Given the following conversation and a follow up question, rephrase the follow up question to be a standalone question, in its original language."
    "If the follow up question does not need context, return the exact same text back."
    "Never rephrase the follow up question given the chat history unless the follow up question needs context."
    "Do not answer the question"
    "Chat History: {chat_history}"
    "Follow Up question: {input}"
    "Standalone question:"
)

condense_question_prompt = ChatPromptTemplate.from_messages(
    [
        ("system", condense_question_system_template),
        ("placeholder", "{chat_history}"),
        ("human", "{input}"),
    ]
)

qa_prompt = ChatPromptTemplate.from_messages(
    [
        ("system", system_prompt),
        ("placeholder", "{chat_history}"),
        ("human", "{input}"),
    ]
)


def format_docs(docs):
    return "\n\n".join(doc.page_content for doc in docs)


def get_adjacent_chunks(reference_docs):
    try: 
        conn = pool.getconn()
        cur = conn.cursor(cursor_factory=extras.RealDictCursor)
        sources = []
        for doc in reference_docs:
            if str(doc.metadata["source"]) not in sources:
                sources.append(str(doc.metadata["source"]))
        query = """SELECT "document", "cmetadata" FROM public.langchain_pg_embedding
                    WHERE cmetadata ->> 'source' IN %s"""
        cur.execute(query, (tuple(sources)[:2],))
        docs = cur.fetchall()
        context = "\n\n".join(doc["document"] for doc in docs)
        links = {str(doc["cmetadata"]["fd_article"]) for doc in docs}
        return {"context": context, "sources": [{"fd_article": link} for link in list(links)[:2]]}
    except Exception as e:
        print(e)
        conn.rollback()
        raise Exception(e)
    finally:
        cur.close()
        conn.close()
        pool.putconn(conn)

def get_release_notes_context(data):
    try: 
        conn = pool.getconn()
        cur = conn.cursor(cursor_factory=extras.RealDictCursor)

        query = """SELECT "document" FROM public.langchain_pg_embedding
                    WHERE cmetadata ->> 'fd_article' = %s"""
        cur.execute(query, [os.environ["LATEST_SDK_ARTICLE"]])
        docs = cur.fetchall()
        context = "\n\n".join(doc["document"] for doc in docs)
        data["context"] = context
        return data
    except Exception as e:
        print(e)
        conn.rollback()
        raise Exception(e)
    finally:
        cur.close()
        conn.close()
        pool.putconn(conn)    

def create_retriever(user_type, category):
    if category and category in vectorstores.keys():
        vectorstores[category]["weight"] = 0.8

    retrievers = []
    weights = []

    for key in vectorstores.keys():
        if key == "internal" and user_type == "internal":
            retrievers.append(vectorstores[key]["retriever"])
            weights.append(0.6)
        else:
            retrievers.append(vectorstores[key]["retriever"])
            weights.append(vectorstores[key]["weight"])

    ensemble_retriever = EnsembleRetriever(retrievers=retrievers, weights=weights)

    filter = EmbeddingsRedundantFilter(embeddings=OpenAIEmbeddings())
    reorder = LongContextReorder()
    relevant_filter = EmbeddingsFilter(
        embeddings=OpenAIEmbeddings(), similarity_threshold=0.75
    )

    pipeline = DocumentCompressorPipeline(
        transformers=[filter, reorder, relevant_filter]
    )
    compression_retriever_reordered = ContextualCompressionRetriever(
        base_compressor=pipeline,
        base_retriever=ensemble_retriever,
        search_kwargs={"k": 20},
    )
    history_aware_retriever = create_history_aware_retriever(
        llm, compression_retriever_reordered, condense_question_prompt
    )
    return history_aware_retriever


class InMemoryHistory(BaseChatMessageHistory, BaseModel):
    """In memory implementation of chat message history."""

    messages: List[BaseMessage] = Field(default_factory=list)

    def add_messages(self, messages: List[BaseMessage]) -> None:
        """Add a list of messages to the store"""
        self.messages.extend(messages)

    def clear(self) -> None:
        self.messages = []


store = {}


def get_by_session_id(session_id: str) -> BaseChatMessageHistory:
    if session_id not in store:
        store[session_id] = InMemoryHistory()
    return store[session_id]


def get_router_chain():
    chain = (
        ChatPromptTemplate.from_template(
            """Given the user question below, classify it as either being about `Greetings` if it has greetings or acknowldgements, 
            `release notes` if its about latest SDK/APK version  or `Other`.

            Do not respond with more than one word.

            <question>
            {input}
            </question>

            Classification:"""
        )
        | llm
        | StrOutputParser()
    )
    return chain


def get_general_chain():
    general_chain = (
        ChatPromptTemplate.from_template(
            """Respond to the following Question if it is greetings, acknowledgments, and thank-you messages appropriately and politely. 
        Do not answer any other Question.
        Question: {input}
        Answer:"""
        )
        | llm
        | StrOutputParser()
    )
    return general_chain


def get_release_notes():
    release_notes_chain = (
        RunnablePassthrough.assign(context=get_release_notes_context) | ChatPromptTemplate.from_template(
            "You are an AI assistant designed to answer questions using the provided context."
            "The context below is release notes of latest SDK version & latest APK version though there is no direct mention of words SDK & APK"
            "Use the words SDK & APK synonymously"
            "Your response should strictly adhere to the context and be clear, concise, and structured. Please follow these guidelines:"
            "1. **Relevance**: Use only the provided context to answer the question. Do not answer the question outside the context given even if you know the answer to it. If the context does not contain enough information, respond: *'Unfortunately, I am unable to answer that question'* Do not guess or fabricate answers."
            "2. **Clarity and Completeness**:"
            "- Provide complete answers wherever possible."
            "- Break down complex responses into bullet points or numbered steps for better readability."
            "Unless absolutely neccesary and it is relevant to the question, always limit the characters in the final answer to under 200 characters"
            "Dont justify your answers. Dont give information not mentioned in the context"
            "\n question: {input}"
            "\n context: {context}"
        )
        | llm
        | StrOutputParser()
    )
    return release_notes_chain

def analyze_data(data):
    chain = (
        ChatPromptTemplate.from_template(
            """Given the user question below, and answer to the question obtianed, classify it as either being `OK` if question is answered and `NO` otherwise.

            Do not respond with more than one word.

            <question>
            {input}
            </question>
            <answer>
            {answer}
            </answer>

            Classification:"""
        )
        | llm
        | StrOutputParser()
    )
    res = chain.invoke({"input": data["input"], "answer": data["answer"]})
    data["answered"] = res
    return data

def get_agent_chain(user_type, category):
    search_embeddings = create_retriever(user_type, category)
    general_chain = get_general_chain()
    release_notes_chain = get_release_notes()

    data = search_embeddings | RunnableLambda(get_adjacent_chunks)
    data_chain = RunnablePassthrough.assign(context=data) | qa_prompt | llm
    history_chain = RunnableWithMessageHistory(
        data_chain,
        get_by_session_id,
        input_messages_key="input",
        history_messages_key="chat_history",
    ) | StrOutputParser()
    answer_query_chain = RunnableParallel(
        input=lambda x: x["input"],
        answer=history_chain,
        references=data,
    )

    branch = RunnableBranch(
        (lambda x: "greetings" in x["topic"].lower(), general_chain), 
        (lambda x: "notes" in x["topic"].lower(), release_notes_chain),
        answer_query_chain | RunnableLambda(lambda x: analyze_data(x))
    )
    final_chain = {"topic": get_router_chain(), "input": lambda x: x["input"]} | branch 


    return final_chain.invoke
