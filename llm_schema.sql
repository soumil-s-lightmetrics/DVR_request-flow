--
-- PostgreSQL database dump
--

-- Dumped from database version 14.9 (Ubuntu 14.9-0ubuntu0.22.04.1)
-- Dumped by pg_dump version 14.9 (Ubuntu 14.9-0ubuntu0.22.04.1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: vector; Type: EXTENSION; Schema: -
--

CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;


--
-- Name: EXTENSION vector; Type: COMMENT; Schema: -
--

COMMENT ON EXTENSION vector IS 'vector data type and ivfflat access method';


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: langchain_pg_collection; Type: TABLE; Schema: public
--

CREATE TABLE public.langchain_pg_collection (
    uuid uuid NOT NULL,
    name character varying,
    cmetadata json
);


--
-- Name: langchain_pg_embedding; Type: TABLE; Schema: public
--

CREATE TABLE public.langchain_pg_embedding (
    uuid uuid NOT NULL,
    collection_id uuid,
    embedding public.vector(1536),
    document character varying,
    cmetadata json,
    custom_id character varying,
    source_docs_id integer
);


--
-- Name: llm_category_folder; Type: TABLE; Schema: public
--

CREATE TABLE public.llm_category_folder (
    id integer NOT NULL,
    category text NOT NULL,
    folder text
);


--
-- Name: llm_category_folder_id_seq; Type: SEQUENCE; Schema: public
--

CREATE SEQUENCE public.llm_category_folder_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: llm_category_folder_id_seq; Type: SEQUENCE OWNED BY; Schema: public
--

ALTER SEQUENCE public.llm_category_folder_id_seq OWNED BY public.llm_category_folder.id;


--
-- Name: llm_source_docs; Type: TABLE; Schema: public
--

CREATE TABLE public.llm_source_docs (
    id integer NOT NULL,
    title text,
    category_folder_id integer,
    text_data text
);


--
-- Name: llm_source_docs_id_seq; Type: SEQUENCE; Schema: public
--

CREATE SEQUENCE public.llm_source_docs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: llm_source_docs_id_seq; Type: SEQUENCE OWNED BY; Schema: public
--

ALTER SEQUENCE public.llm_source_docs_id_seq OWNED BY public.llm_source_docs.id;


--
-- Name: llm_category_folder id; Type: DEFAULT; Schema: public
--

ALTER TABLE ONLY public.llm_category_folder ALTER COLUMN id SET DEFAULT nextval('public.llm_category_folder_id_seq'::regclass);


--
-- Name: llm_source_docs id; Type: DEFAULT; Schema: public
--

ALTER TABLE ONLY public.llm_source_docs ALTER COLUMN id SET DEFAULT nextval('public.llm_source_docs_id_seq'::regclass);


--
-- Name: langchain_pg_collection langchain_pg_collection_pkey; Type: CONSTRAINT; Schema: public
--

ALTER TABLE ONLY public.langchain_pg_collection
    ADD CONSTRAINT langchain_pg_collection_pkey PRIMARY KEY (uuid);


--
-- Name: langchain_pg_embedding langchain_pg_embedding_pkey; Type: CONSTRAINT; Schema: public
--

ALTER TABLE ONLY public.langchain_pg_embedding
    ADD CONSTRAINT langchain_pg_embedding_pkey PRIMARY KEY (uuid);


--
-- Name: llm_category_folder llm_category_folder_pkey; Type: CONSTRAINT; Schema: public
--

ALTER TABLE ONLY public.llm_category_folder
    ADD CONSTRAINT llm_category_folder_pkey PRIMARY KEY (id);


--
-- Name: llm_source_docs llm_source_docs_pkey; Type: CONSTRAINT; Schema: public
--

ALTER TABLE ONLY public.llm_source_docs
    ADD CONSTRAINT llm_source_docs_pkey PRIMARY KEY (id);


--
-- Name: llm_category_folder u_category_folder; Type: CONSTRAINT; Schema: public
--

ALTER TABLE ONLY public.llm_category_folder
    ADD CONSTRAINT u_category_folder UNIQUE (category, folder);


--
-- Name: llm_source_docs fk_source_category; Type: FK CONSTRAINT; Schema: public
--

ALTER TABLE ONLY public.llm_source_docs
    ADD CONSTRAINT fk_source_category FOREIGN KEY (category_folder_id) REFERENCES public.llm_category_folder(id);


--
-- Name: langchain_pg_embedding fk_source_docs_id; Type: FK CONSTRAINT; Schema: public
--

ALTER TABLE ONLY public.langchain_pg_embedding
    ADD CONSTRAINT fk_source_docs_id FOREIGN KEY (source_docs_id) REFERENCES public.llm_source_docs(id);


--
-- Name: langchain_pg_embedding langchain_pg_embedding_collection_id_fkey; Type: FK CONSTRAINT; Schema: public
--

ALTER TABLE ONLY public.langchain_pg_embedding
    ADD CONSTRAINT langchain_pg_embedding_collection_id_fkey FOREIGN KEY (collection_id) REFERENCES public.langchain_pg_collection(uuid) ON DELETE CASCADE;

--
-- Name: freshdesk_article_sync_status; Type: TABLE; Schema: public
--

CREATE TABLE public.freshdesk_article_sync_status (
	fd_article_id int8 NOT NULL,
	openai_file_id text NOT NULL,
	last_synced_at timestamptz NULL,
	is_deleted bool DEFAULT false NULL,
	filename text NULL,
	CONSTRAINT freshdesk_article_sync_status_pkey PRIMARY KEY (fd_article_id)
);

--
-- Name: pinecone_article_sync_status; Type: TABLE; Schema: public
--

CREATE TABLE public.pinecone_article_sync_status (
    fd_article_id int8 NOT NULL,
    fd_article_title text NULL,
    vector_count int4 NOT NULL,
    extracted_tags jsonb NULL,
    last_synced_at timestamptz NOT NULL,
    sync_status text NOT NULL DEFAULT 'completed',
    error_message text NULL,
    content_hash text NULL,
    created_at timestamptz DEFAULT NOW(),
    updated_at timestamptz DEFAULT NOW(),
    CONSTRAINT pinecone_article_sync_status_pkey PRIMARY KEY (fd_article_id)
);

CREATE INDEX idx_pinecone_last_synced
    ON public.pinecone_article_sync_status(last_synced_at);

CREATE INDEX idx_pinecone_status
    ON public.pinecone_article_sync_status(sync_status);

CREATE INDEX idx_pinecone_tags
    ON public.pinecone_article_sync_status USING gin(extracted_tags);

--
-- Name: pinecone_sync_runs; Type: TABLE; Schema: public
--

CREATE TABLE public.pinecone_sync_runs (
    id serial NOT NULL,
    run_started_at timestamptz NOT NULL,
    run_completed_at timestamptz NULL,
    total_articles_checked int4 NULL,
    articles_synced int4 NULL,
    articles_failed int4 NULL,
    total_chunks_processed int4 NULL,
    total_vectors_upserted int4 NULL,
    llm_api_calls int4 NULL,
    llm_cost_estimate decimal(10, 4) NULL,
    status text NULL,
    error_message text NULL,
    CONSTRAINT pinecone_sync_runs_pkey PRIMARY KEY (id)
);

--
-- PostgreSQL database dump complete
--

