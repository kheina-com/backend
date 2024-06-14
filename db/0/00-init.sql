--
-- PostgreSQL database dump
--

-- Dumped from database version 12.16
-- Dumped by pg_dump version 14.3

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
-- Name: auth; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA auth;


--
-- Name: pgcrypto; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;


--
-- Name: EXTENSION pgcrypto; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION pgcrypto IS 'cryptographic functions';


--
-- Name: add_tags(character, bigint, text[]); Type: PROCEDURE; Schema: public; Owner: -
--

CREATE PROCEDURE public.add_tags(sp_post_id character, sp_user_id bigint, sp_tags text[])
    LANGUAGE plpgsql
    AS $$
BEGIN

INSERT INTO kheina.public.tag_post
(tag_id, user_id, post_id)
WITH sp_tag_ids AS (
SELECT tag_to_id(unnest(sp_tags)) AS tag_id
)
SELECT sp_tag_ids.tag_id, sp_user_id, sp_post_id
FROM sp_tag_ids
UNION
SELECT tag_inheritance.child, sp_user_id, sp_post_id
FROM sp_tag_ids
INNER JOIN kheina.public.tag_inheritance
ON tag_inheritance.parent = sp_tag_ids.tag_id
ON CONFLICT DO NOTHING;

END;
$$;


--
-- Name: add_tags(bigint, bigint, text[]); Type: PROCEDURE; Schema: public; Owner: -
--

CREATE PROCEDURE public.add_tags(sp_post_id bigint, sp_user_id bigint, sp_tags text[])
    LANGUAGE plpgsql
    AS $$
BEGIN

INSERT INTO kheina.public.tag_post
(tag_id, user_id, post_id)
WITH sp_tag_ids AS (
SELECT tag_to_id(unnest(sp_tags)) AS tag_id
)
SELECT sp_tag_ids.tag_id, sp_user_id, sp_post_id
FROM sp_tag_ids
UNION
SELECT tag_inheritance.child, sp_user_id, sp_post_id
FROM sp_tag_ids
INNER JOIN kheina.public.tag_inheritance
ON tag_inheritance.parent = sp_tag_ids.tag_id
ON CONFLICT DO NOTHING;

END;
$$;


--
-- Name: context_to_id(text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.context_to_id(_frame text) RETURNS smallint
    LANGUAGE sql
    AS $$
SELECT context_id FROM context WHERE frame = _frame LIMIT 1;
$$;


--
-- Name: create_new_post(bigint); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.create_new_post(sp_user_id bigint) RETURNS character
    LANGUAGE plpgsql
    AS $$
DECLARE
sp_post_id CHAR(8);
BEGIN

sp_post_id := (
SELECT posts.post_id FROM kheina.public.posts
WHERE posts.uploader = sp_user_id
AND privacy_id = privacy_to_id('unpublished')
LIMIT 1
);

IF sp_post_id IS NULL THEN
INSERT INTO kheina.public.posts
(uploader)
VALUES
(sp_user_id)
RETURNING post_id INTO sp_post_id;
END IF;

RETURN sp_post_id;

END;
$$;


--
-- Name: inherit_tag(bigint, text, text, boolean); Type: PROCEDURE; Schema: public; Owner: -
--

CREATE PROCEDURE public.inherit_tag(sp_user_id bigint, sp_parent text, sp_child text, sp_deprecate boolean)
    LANGUAGE plpgsql
    AS $$
DECLARE sp_parent_id INT;
DECLARE sp_child_id INT;
BEGIN

sp_parent_id := kheina.public.tag_to_id(sp_parent);
sp_child_id := kheina.public.tag_to_id(sp_child);

INSERT INTO kheina.public.tag_inheritance
(parent, child)
SELECT sp_parent_id, sp_child_id
UNION
SELECT sp_parent_id, tag_inheritance.child
FROM kheina.public.tag_inheritance
WHERE tag_inheritance.parent = sp_child_id
ON CONFLICT DO NOTHING;

INSERT INTO kheina.public.tag_post
(tag_id, post_id, user_id)
SELECT tag_inheritance.child, tag_post.post_id, tag_post.user_id
FROM kheina.public.tag_inheritance
INNER JOIN kheina.public.tag_post
ON tag_post.tag_id = tag_inheritance.parent
WHERE tag_inheritance.parent = sp_parent_id
ON CONFLICT DO NOTHING;

IF sp_deprecate THEN
UPDATE kheina.public.tags
SET deprecated = true
WHERE tags.tag_id = sp_parent_id;

END IF;

END;
$$;


--
-- Name: media_file_type_to_id(text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.media_file_type_to_id(_type text) RETURNS smallint
    LANGUAGE sql
    AS $$
SELECT media_type_id FROM media_type WHERE file_type = _type LIMIT 1;
$$;


--
-- Name: media_mime_type_to_id(text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.media_mime_type_to_id(_type text) RETURNS smallint
    LANGUAGE sql
    AS $$
SELECT media_type_id FROM media_type WHERE mime_type = _type LIMIT 1;
$$;


--
-- Name: new_post_id(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.new_post_id() RETURNS character
    LANGUAGE plpgsql
    AS $$
DECLARE
fn_post_id CHAR(8);
BEGIN
LOOP
fn_post_id := replace(replace(encode(gen_random_bytes(6), 'base64'), '+', '-'), '/', '_');
EXIT WHEN
(SELECT 1 FROM banned_words WHERE word LIKE concat('%', LOWER(fn_post_id), '%')) IS NULL AND
(SELECT 1 FROM posts WHERE post_id = fn_post_id) IS NULL;
END LOOP;                                                                           
RETURN fn_post_id;
END;
$$;


--
-- Name: privacy_to_id(text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.privacy_to_id(_type text) RETURNS smallint
    LANGUAGE sql
    AS $$
SELECT privacy_id FROM privacy WHERE type = _type LIMIT 1;
$$;


--
-- Name: rating_to_id(text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.rating_to_id(_rating text) RETURNS smallint
    LANGUAGE sql
    AS $$
SELECT ratings.rating_id FROM kheina.public.ratings WHERE ratings.rating = LOWER(_rating) LIMIT 1;
$$;


--
-- Name: relation_to_id(text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.relation_to_id(_type text) RETURNS smallint
    LANGUAGE sql
    AS $$
SELECT relation_id FROM relations WHERE relation = _type LIMIT 1;
$$;


--
-- Name: remove_tags(character, bigint, text[]); Type: PROCEDURE; Schema: public; Owner: -
--

CREATE PROCEDURE public.remove_tags(sp_post_id character, sp_user_id bigint, sp_tags text[])
    LANGUAGE plpgsql
    AS $$
DECLARE sp_user_ids BIGINT[];
BEGIN

sp_user_ids := (WITH users AS (
SELECT user_id
FROM kheina.public.user_post
WHERE post_id = sp_post_id
UNION
SELECT uploader AS user_id
FROM kheina.public.posts
WHERE post_id = sp_post_id
)
SELECT array_agg(user_id)
FROM users);

IF sp_user_id = any(sp_user_ids) THEN
DELETE FROM kheina.public.tag_post
USING kheina.public.tags
WHERE tag_post.tag_id = tags.tag_id
AND tag_post.post_id = sp_post_id
AND tags.tag = any(sp_tags);

ELSE
DELETE FROM kheina.public.tag_post
USING kheina.public.tags
WHERE tag_post.tag_id = tags.tag_id
AND tag_post.post_id = sp_post_id
AND tags.tag = any(sp_tags)
AND tag_post.user_id != any(sp_user_ids);
END IF;

END;
$$;


--
-- Name: remove_tags(bigint, bigint, text[]); Type: PROCEDURE; Schema: public; Owner: -
--

CREATE PROCEDURE public.remove_tags(sp_post_id bigint, sp_user_id bigint, sp_tags text[])
    LANGUAGE plpgsql
    AS $$
DECLARE sp_user_ids BIGINT[];
BEGIN

sp_user_ids := (WITH users AS (
SELECT user_id
FROM kheina.public.user_post
WHERE post_id = sp_post_id
UNION
SELECT uploader AS user_id
FROM kheina.public.posts
WHERE post_id = sp_post_id
)
SELECT array_agg(user_id)
FROM users);

IF sp_user_id = any(sp_user_ids) THEN
DELETE FROM kheina.public.tag_post
USING kheina.public.tags
WHERE tag_post.tag_id = tags.tag_id
AND tag_post.post_id = sp_post_id
AND tags.tag = any(sp_tags);

ELSE
DELETE FROM kheina.public.tag_post
USING kheina.public.tags
WHERE tag_post.tag_id = tags.tag_id
AND tag_post.post_id = sp_post_id
AND tags.tag = any(sp_tags)
AND tag_post.user_id != any(sp_user_ids);
END IF;

END;
$$;


--
-- Name: tag_class_to_id(text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.tag_class_to_id(_class text) RETURNS smallint
    LANGUAGE sql
    AS $$
SELECT class_id FROM tag_classes WHERE class = _class LIMIT 1;
$$;


--
-- Name: tag_to_id(text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.tag_to_id(_tag text) RETURNS integer
    LANGUAGE plpgsql
    AS $$
DECLARE
fn_tag_id INT;
BEGIN
fn_tag_id := (SELECT tag_id FROM tags WHERE tag = _tag LIMIT 1);
IF fn_tag_id IS NULL THEN
INSERT INTO tags (tag)
VALUES (_tag)
RETURNING tag_id INTO fn_tag_id;
END IF;
RETURN fn_tag_id;
END;
$$;


--
-- Name: user_to_id(text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.user_to_id(_handle text) RETURNS bigint
    LANGUAGE sql
    AS $$
SELECT user_id FROM users WHERE handle = _handle LIMIT 1;
$$;


--
-- Name: user_upload_file(bigint, character, text, text); Type: PROCEDURE; Schema: public; Owner: -
--

CREATE PROCEDURE public.user_upload_file(sp_user_id bigint, INOUT sp_post_id character, sp_media_type text, sp_filename text)
    LANGUAGE plpgsql
    AS $$
BEGIN

IF sp_post_id IS NULL THEN
sp_post_id := (SELECT kheina.public.create_new_post(sp_user_id));
END IF;

UPDATE kheina.public.posts
SET updated_on = NOW(),
media_type_id = media_mime_type_to_id(sp_media_type),
filename = sp_filename
WHERE post_id = sp_post_id
AND uploader = sp_user_id;

END;
$$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: bot_login; Type: TABLE; Schema: auth; Owner: -
--

CREATE TABLE auth.bot_login (
    bot_id bigint NOT NULL,
    user_id bigint,
    password bytea NOT NULL,
    secret smallint NOT NULL,
    bot_type_id smallint NOT NULL,
    created_by bigint NOT NULL
);


--
-- Name: bot_login_bot_id_seq; Type: SEQUENCE; Schema: auth; Owner: -
--

ALTER TABLE auth.bot_login ALTER COLUMN bot_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME auth.bot_login_bot_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: bot_type; Type: TABLE; Schema: auth; Owner: -
--

CREATE TABLE auth.bot_type (
    bot_type_id smallint NOT NULL,
    bot_type text NOT NULL
);


--
-- Name: bot_type_bot_type_id_seq; Type: SEQUENCE; Schema: auth; Owner: -
--

ALTER TABLE auth.bot_type ALTER COLUMN bot_type_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME auth.bot_type_bot_type_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: token_keys; Type: TABLE; Schema: auth; Owner: -
--

CREATE TABLE auth.token_keys (
    key_id integer NOT NULL,
    algorithm text NOT NULL,
    public_key bytea NOT NULL,
    signature bytea NOT NULL,
    issued timestamp with time zone DEFAULT now() NOT NULL,
    expires timestamp with time zone DEFAULT (CURRENT_DATE + '30 days'::interval) NOT NULL
);


--
-- Name: token_keys_key_id_seq; Type: SEQUENCE; Schema: auth; Owner: -
--

ALTER TABLE auth.token_keys ALTER COLUMN key_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME auth.token_keys_key_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: user_login; Type: TABLE; Schema: auth; Owner: -
--

CREATE TABLE auth.user_login (
    user_id bigint NOT NULL,
    email_hash bytea NOT NULL,
    password bytea NOT NULL,
    secret smallint NOT NULL
);


--
-- Name: avro_schemas; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.avro_schemas (
    fingerprint bigint NOT NULL,
    schema bytea NOT NULL
);


--
-- Name: badges; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.badges (
    badge_id smallint NOT NULL,
    emoji text NOT NULL,
    label text NOT NULL
);


--
-- Name: badges_badge_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.badges ALTER COLUMN badge_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.badges_badge_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: banned_words; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.banned_words (
    word text NOT NULL,
    context_id smallint NOT NULL
);


--
-- Name: configs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.configs (
    key text NOT NULL,
    value text,
    created_on timestamp with time zone DEFAULT now() NOT NULL,
    updated_on timestamp with time zone DEFAULT now() NOT NULL,
    updated_by bigint NOT NULL,
    bytes bytea
);


--
-- Name: context; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.context (
    context_id smallint NOT NULL,
    frame text NOT NULL
);


--
-- Name: context_context_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.context ALTER COLUMN context_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.context_context_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: following; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.following (
    user_id bigint NOT NULL,
    follows bigint NOT NULL
);


--
-- Name: media_type; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.media_type (
    media_type_id smallint NOT NULL,
    file_type text NOT NULL,
    mime_type text NOT NULL
);


--
-- Name: media_type_media_type_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.media_type ALTER COLUMN media_type_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.media_type_media_type_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: post_scores; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.post_scores (
    upvotes integer NOT NULL,
    downvotes integer NOT NULL,
    top integer NOT NULL,
    hot double precision NOT NULL,
    best double precision NOT NULL,
    controversial double precision NOT NULL,
    post_id bigint NOT NULL
);


--
-- Name: post_votes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.post_votes (
    user_id bigint NOT NULL,
    upvote boolean,
    created_on timestamp with time zone DEFAULT now() NOT NULL,
    post_id bigint NOT NULL
);


--
-- Name: posts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.posts (
    uploader bigint NOT NULL,
    created_on timestamp with time zone DEFAULT now() NOT NULL,
    updated_on timestamp with time zone DEFAULT now() NOT NULL,
    media_type_id smallint,
    privacy_id smallint DEFAULT public.privacy_to_id('unpublished'::text) NOT NULL,
    title text,
    description text,
    filename text,
    rating smallint DEFAULT public.rating_to_id('explicit'::text) NOT NULL,
    width integer,
    height integer,
    post_id bigint NOT NULL,
    parent bigint
);


--
-- Name: privacy; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.privacy (
    privacy_id smallint NOT NULL,
    type text NOT NULL
);


--
-- Name: privacy_privacy_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.privacy ALTER COLUMN privacy_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.privacy_privacy_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: ratings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ratings (
    rating_id smallint NOT NULL,
    rating text NOT NULL
);


--
-- Name: ratings_rating_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.ratings ALTER COLUMN rating_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.ratings_rating_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: relations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.relations (
    relation_id smallint NOT NULL,
    relation text NOT NULL
);


--
-- Name: relations_relation_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.relations ALTER COLUMN relation_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.relations_relation_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: set_post; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.set_post (
    set_id bigint NOT NULL,
    post_id bigint NOT NULL,
    index integer NOT NULL
);


--
-- Name: sets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sets (
    set_id bigint NOT NULL,
    owner bigint NOT NULL,
    title text,
    description text,
    privacy smallint NOT NULL,
    created timestamp with time zone DEFAULT now() NOT NULL,
    updated timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: tag_assist; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tag_assist (
    string text NOT NULL,
    tag_id integer NOT NULL
);


--
-- Name: tag_blocking; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tag_blocking (
    user_id bigint NOT NULL,
    blocked bigint NOT NULL
);


--
-- Name: tag_classes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tag_classes (
    class_id smallint NOT NULL,
    class text NOT NULL
);


--
-- Name: tag_classes_class_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.tag_classes ALTER COLUMN class_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.tag_classes_class_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: tag_inheritance; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tag_inheritance (
    parent integer NOT NULL,
    child integer NOT NULL
);


--
-- Name: tag_post; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tag_post (
    tag_id integer NOT NULL,
    user_id bigint NOT NULL,
    post_id bigint NOT NULL
);


--
-- Name: tags; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tags (
    tag_id bigint NOT NULL,
    tag text NOT NULL,
    deprecated boolean DEFAULT false NOT NULL,
    class_id smallint DEFAULT public.tag_class_to_id('misc'::text) NOT NULL,
    owner bigint,
    description text
);


--
-- Name: tags_tag_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.tags ALTER COLUMN tag_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.tags_tag_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: user_badge; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_badge (
    user_id bigint NOT NULL,
    badge_id smallint NOT NULL
);


--
-- Name: user_blocking; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_blocking (
    user_id bigint NOT NULL,
    blocked bigint NOT NULL
);


--
-- Name: user_post; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_post (
    post_id bigint NOT NULL,
    user_id bigint NOT NULL
);


--
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    user_id bigint NOT NULL,
    display_name text NOT NULL,
    handle text NOT NULL,
    privacy_id smallint DEFAULT public.privacy_to_id('public'::text) NOT NULL,
    description text,
    website text,
    created_on timestamp with time zone DEFAULT now() NOT NULL,
    mod boolean DEFAULT false NOT NULL,
    admin boolean DEFAULT false NOT NULL,
    verified boolean DEFAULT false NOT NULL,
    icon bigint,
    banner bigint
);


--
-- Name: users_user_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.users ALTER COLUMN user_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.users_user_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: bot_login bot_login_pkey; Type: CONSTRAINT; Schema: auth; Owner: -
--

ALTER TABLE ONLY auth.bot_login
    ADD CONSTRAINT bot_login_pkey PRIMARY KEY (bot_id);


--
-- Name: bot_type bot_type_pkey; Type: CONSTRAINT; Schema: auth; Owner: -
--

ALTER TABLE ONLY auth.bot_type
    ADD CONSTRAINT bot_type_pkey PRIMARY KEY (bot_type_id);


--
-- Name: token_keys token_keys_key_id_key; Type: CONSTRAINT; Schema: auth; Owner: -
--

ALTER TABLE ONLY auth.token_keys
    ADD CONSTRAINT token_keys_key_id_key UNIQUE (key_id);


--
-- Name: token_keys token_keys_pkey; Type: CONSTRAINT; Schema: auth; Owner: -
--

ALTER TABLE ONLY auth.token_keys
    ADD CONSTRAINT token_keys_pkey PRIMARY KEY (algorithm, key_id);


--
-- Name: user_login user_login_email_hash_key; Type: CONSTRAINT; Schema: auth; Owner: -
--

ALTER TABLE ONLY auth.user_login
    ADD CONSTRAINT user_login_email_hash_key UNIQUE (email_hash);


--
-- Name: user_login user_login_pkey; Type: CONSTRAINT; Schema: auth; Owner: -
--

ALTER TABLE ONLY auth.user_login
    ADD CONSTRAINT user_login_pkey PRIMARY KEY (user_id);


--
-- Name: avro_schemas avro_schemas_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.avro_schemas
    ADD CONSTRAINT avro_schemas_pkey PRIMARY KEY (fingerprint);


--
-- Name: badges badges_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.badges
    ADD CONSTRAINT badges_pkey PRIMARY KEY (badge_id);


--
-- Name: banned_words banned_words_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.banned_words
    ADD CONSTRAINT banned_words_pkey PRIMARY KEY (context_id, word);


--
-- Name: banned_words banned_words_word_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.banned_words
    ADD CONSTRAINT banned_words_word_key UNIQUE (word);


--
-- Name: configs configs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.configs
    ADD CONSTRAINT configs_pkey PRIMARY KEY (key);


--
-- Name: context context_frame_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.context
    ADD CONSTRAINT context_frame_key UNIQUE (frame);


--
-- Name: context context_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.context
    ADD CONSTRAINT context_pkey PRIMARY KEY (context_id);


--
-- Name: following following_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.following
    ADD CONSTRAINT following_pkey PRIMARY KEY (user_id, follows);


--
-- Name: media_type media_type_mime_type_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.media_type
    ADD CONSTRAINT media_type_mime_type_key UNIQUE (mime_type);


--
-- Name: media_type media_type_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.media_type
    ADD CONSTRAINT media_type_pkey PRIMARY KEY (media_type_id);


--
-- Name: media_type media_type_type_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.media_type
    ADD CONSTRAINT media_type_type_key UNIQUE (file_type);


--
-- Name: post_scores post_scores_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.post_scores
    ADD CONSTRAINT post_scores_pkey PRIMARY KEY (post_id);


--
-- Name: post_votes post_votes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.post_votes
    ADD CONSTRAINT post_votes_pkey PRIMARY KEY (user_id, post_id);


--
-- Name: posts posts_id_pk; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.posts
    ADD CONSTRAINT posts_id_pk UNIQUE (post_id);


--
-- Name: posts posts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.posts
    ADD CONSTRAINT posts_pkey PRIMARY KEY (post_id);


--
-- Name: privacy privacy_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.privacy
    ADD CONSTRAINT privacy_pkey PRIMARY KEY (privacy_id);


--
-- Name: privacy privacy_type_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.privacy
    ADD CONSTRAINT privacy_type_key UNIQUE (type);


--
-- Name: ratings ratings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ratings
    ADD CONSTRAINT ratings_pkey PRIMARY KEY (rating_id);


--
-- Name: ratings ratings_rating_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ratings
    ADD CONSTRAINT ratings_rating_key UNIQUE (rating);


--
-- Name: relations relations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.relations
    ADD CONSTRAINT relations_pkey PRIMARY KEY (relation_id);


--
-- Name: relations relations_type_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.relations
    ADD CONSTRAINT relations_type_key UNIQUE (relation);


--
-- Name: set_post set_post_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.set_post
    ADD CONSTRAINT set_post_pkey PRIMARY KEY (set_id, post_id);


--
-- Name: set_post set_post_post_id_set_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.set_post
    ADD CONSTRAINT set_post_post_id_set_id_key UNIQUE (post_id, set_id);


--
-- Name: set_post set_post_set_id_index_post_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.set_post
    ADD CONSTRAINT set_post_set_id_index_post_id_key UNIQUE (set_id, index) INCLUDE (post_id);


--
-- Name: sets sets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sets
    ADD CONSTRAINT sets_pkey PRIMARY KEY (set_id);


--
-- Name: tag_assist tag_assist_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tag_assist
    ADD CONSTRAINT tag_assist_pkey PRIMARY KEY (string, tag_id);


--
-- Name: tag_blocking tag_blocking_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tag_blocking
    ADD CONSTRAINT tag_blocking_pkey PRIMARY KEY (user_id, blocked);


--
-- Name: tag_classes tag_classes_class_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tag_classes
    ADD CONSTRAINT tag_classes_class_key UNIQUE (class);


--
-- Name: tag_classes tag_classes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tag_classes
    ADD CONSTRAINT tag_classes_pkey PRIMARY KEY (class_id);


--
-- Name: tag_inheritance tag_inheritance_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tag_inheritance
    ADD CONSTRAINT tag_inheritance_pkey PRIMARY KEY (parent, child);


--
-- Name: tag_post tag_post_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tag_post
    ADD CONSTRAINT tag_post_pkey PRIMARY KEY (post_id, tag_id);


--
-- Name: tags tags_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tags
    ADD CONSTRAINT tags_pkey PRIMARY KEY (tag_id);


--
-- Name: tags tags_tag_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tags
    ADD CONSTRAINT tags_tag_key UNIQUE (tag);


--
-- Name: user_badge user_badge_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_badge
    ADD CONSTRAINT user_badge_pkey PRIMARY KEY (user_id, badge_id);


--
-- Name: user_blocking user_blocking_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_blocking
    ADD CONSTRAINT user_blocking_pkey PRIMARY KEY (user_id, blocked);


--
-- Name: user_post user_post_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_post
    ADD CONSTRAINT user_post_pkey PRIMARY KEY (post_id, user_id);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (user_id);


--
-- Name: bot_login_created_by_index; Type: INDEX; Schema: auth; Owner: -
--

CREATE INDEX bot_login_created_by_index ON auth.bot_login USING btree (created_by);


--
-- Name: bot_login_user_id_bot_id_joint_index; Type: INDEX; Schema: auth; Owner: -
--

CREATE UNIQUE INDEX bot_login_user_id_bot_id_joint_index ON auth.bot_login USING btree (user_id, bot_id);


--
-- Name: bot_login_user_id_index; Type: INDEX; Schema: auth; Owner: -
--

CREATE UNIQUE INDEX bot_login_user_id_index ON auth.bot_login USING btree (user_id) WHERE (user_id IS NOT NULL);


--
-- Name: token_keys_algorithm_issued_expires_joint_index; Type: INDEX; Schema: auth; Owner: -
--

CREATE INDEX token_keys_algorithm_issued_expires_joint_index ON auth.token_keys USING btree (algorithm, issued, expires);


--
-- Name: following_follows_user_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX following_follows_user_id_idx ON public.following USING btree (follows, user_id);


--
-- Name: posts_uploader_unpublished_constraint; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX posts_uploader_unpublished_constraint ON public.posts USING btree (uploader, privacy_id) WHERE (privacy_id = 4);


--
-- Name: sets_owner_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX sets_owner_idx ON public.sets USING btree (owner);


--
-- Name: tag_inheritance_child_parent_joint_index; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX tag_inheritance_child_parent_joint_index ON public.tag_inheritance USING btree (child, parent);


--
-- Name: tag_post_tag_id_post_id_index; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX tag_post_tag_id_post_id_index ON public.tag_post USING btree (tag_id, post_id);


--
-- Name: tags_deprecated_tag_joint_index; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX tags_deprecated_tag_joint_index ON public.tags USING btree (deprecated, tag);


--
-- Name: users_handle_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX users_handle_key ON public.users USING btree (lower(handle));


--
-- Name: bot_login bot_login_bot_type_id_fkey; Type: FK CONSTRAINT; Schema: auth; Owner: -
--

ALTER TABLE ONLY auth.bot_login
    ADD CONSTRAINT bot_login_bot_type_id_fkey FOREIGN KEY (bot_type_id) REFERENCES auth.bot_type(bot_type_id);


--
-- Name: bot_login bot_login_created_by_fkey; Type: FK CONSTRAINT; Schema: auth; Owner: -
--

ALTER TABLE ONLY auth.bot_login
    ADD CONSTRAINT bot_login_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(user_id);


--
-- Name: bot_login user_id_fk; Type: FK CONSTRAINT; Schema: auth; Owner: -
--

ALTER TABLE ONLY auth.bot_login
    ADD CONSTRAINT user_id_fk FOREIGN KEY (user_id) REFERENCES public.users(user_id);


--
-- Name: user_login user_login_user_id_fkey; Type: FK CONSTRAINT; Schema: auth; Owner: -
--

ALTER TABLE ONLY auth.user_login
    ADD CONSTRAINT user_login_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(user_id);


--
-- Name: banned_words banned_words_context_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.banned_words
    ADD CONSTRAINT banned_words_context_id_fkey FOREIGN KEY (context_id) REFERENCES public.context(context_id);


--
-- Name: configs configs_updated_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.configs
    ADD CONSTRAINT configs_updated_by_fkey FOREIGN KEY (updated_by) REFERENCES public.users(user_id);


--
-- Name: user_badge fk_badge_id; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_badge
    ADD CONSTRAINT fk_badge_id FOREIGN KEY (badge_id) REFERENCES public.badges(badge_id);


--
-- Name: user_badge fk_user_id; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_badge
    ADD CONSTRAINT fk_user_id FOREIGN KEY (user_id) REFERENCES public.users(user_id);


--
-- Name: following following_follows_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.following
    ADD CONSTRAINT following_follows_fkey FOREIGN KEY (follows) REFERENCES public.users(user_id);


--
-- Name: following following_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.following
    ADD CONSTRAINT following_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(user_id);


--
-- Name: post_scores post_scores_post_id_int_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.post_scores
    ADD CONSTRAINT post_scores_post_id_int_fkey FOREIGN KEY (post_id) REFERENCES public.posts(post_id);


--
-- Name: post_votes post_votes_post_id_int_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.post_votes
    ADD CONSTRAINT post_votes_post_id_int_fkey FOREIGN KEY (post_id) REFERENCES public.posts(post_id);


--
-- Name: post_votes post_votes_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.post_votes
    ADD CONSTRAINT post_votes_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(user_id);


--
-- Name: posts posts_media_type_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.posts
    ADD CONSTRAINT posts_media_type_id_fkey FOREIGN KEY (media_type_id) REFERENCES public.media_type(media_type_id);


--
-- Name: posts posts_parent_int_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.posts
    ADD CONSTRAINT posts_parent_int_fkey FOREIGN KEY (parent) REFERENCES public.posts(post_id);


--
-- Name: posts posts_privacy_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.posts
    ADD CONSTRAINT posts_privacy_id_fkey FOREIGN KEY (privacy_id) REFERENCES public.privacy(privacy_id);


--
-- Name: posts posts_rating_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.posts
    ADD CONSTRAINT posts_rating_fkey FOREIGN KEY (rating) REFERENCES public.ratings(rating_id);


--
-- Name: posts posts_uploader_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.posts
    ADD CONSTRAINT posts_uploader_fkey FOREIGN KEY (uploader) REFERENCES public.users(user_id);


--
-- Name: set_post set_post_post_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.set_post
    ADD CONSTRAINT set_post_post_id_fkey FOREIGN KEY (post_id) REFERENCES public.posts(post_id);


--
-- Name: set_post set_post_set_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.set_post
    ADD CONSTRAINT set_post_set_id_fkey FOREIGN KEY (set_id) REFERENCES public.sets(set_id);


--
-- Name: sets sets_owner_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sets
    ADD CONSTRAINT sets_owner_fkey FOREIGN KEY (owner) REFERENCES public.users(user_id);


--
-- Name: sets sets_privacy_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sets
    ADD CONSTRAINT sets_privacy_fkey FOREIGN KEY (privacy) REFERENCES public.privacy(privacy_id);


--
-- Name: tag_assist tag_assist_tag_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tag_assist
    ADD CONSTRAINT tag_assist_tag_id_fkey FOREIGN KEY (tag_id) REFERENCES public.tags(tag_id);


--
-- Name: tag_blocking tag_blocking_blocked_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tag_blocking
    ADD CONSTRAINT tag_blocking_blocked_fkey FOREIGN KEY (blocked) REFERENCES public.tags(tag_id);


--
-- Name: tag_blocking tag_blocking_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tag_blocking
    ADD CONSTRAINT tag_blocking_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(user_id);


--
-- Name: tag_inheritance tag_inheritance_child_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tag_inheritance
    ADD CONSTRAINT tag_inheritance_child_fkey FOREIGN KEY (child) REFERENCES public.tags(tag_id);


--
-- Name: tag_inheritance tag_inheritance_parent_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tag_inheritance
    ADD CONSTRAINT tag_inheritance_parent_fkey FOREIGN KEY (parent) REFERENCES public.tags(tag_id);


--
-- Name: tag_post tag_post_post_id_int_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tag_post
    ADD CONSTRAINT tag_post_post_id_int_fkey FOREIGN KEY (post_id) REFERENCES public.posts(post_id);


--
-- Name: tag_post tag_post_tag_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tag_post
    ADD CONSTRAINT tag_post_tag_id_fkey FOREIGN KEY (tag_id) REFERENCES public.tags(tag_id);


--
-- Name: tag_post tag_post_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tag_post
    ADD CONSTRAINT tag_post_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(user_id);


--
-- Name: tags tags_class_ic_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tags
    ADD CONSTRAINT tags_class_ic_fkey FOREIGN KEY (class_id) REFERENCES public.tag_classes(class_id);


--
-- Name: tags tags_owner_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tags
    ADD CONSTRAINT tags_owner_fkey FOREIGN KEY (owner) REFERENCES public.users(user_id);


--
-- Name: user_blocking user_blocking_blocked_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_blocking
    ADD CONSTRAINT user_blocking_blocked_fkey FOREIGN KEY (blocked) REFERENCES public.users(user_id);


--
-- Name: user_blocking user_blocking_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_blocking
    ADD CONSTRAINT user_blocking_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(user_id);


--
-- Name: user_post user_post_post_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_post
    ADD CONSTRAINT user_post_post_id_fkey FOREIGN KEY (post_id) REFERENCES public.posts(post_id);


--
-- Name: user_post user_post_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_post
    ADD CONSTRAINT user_post_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(user_id);


--
-- Name: users users_banner_int_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_banner_int_fkey FOREIGN KEY (banner) REFERENCES public.posts(post_id);


--
-- Name: users users_icon_int_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_icon_int_fkey FOREIGN KEY (icon) REFERENCES public.posts(post_id);


--
-- Name: users users_privacy_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_privacy_id_fkey FOREIGN KEY (privacy_id) REFERENCES public.privacy(privacy_id);


--
-- PostgreSQL database dump complete
--


ALTER TABLE public.posts ADD COLUMN thumbhash bytea null;


--
-- Name: posts; Type: TABLE; Schema: public; Owner: -
--
