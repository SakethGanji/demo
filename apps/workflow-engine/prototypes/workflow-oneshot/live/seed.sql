-- ===== core_banking =====
\connect core_banking
-- PostgreSQL database dump






-- Name: customers; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.customers (
    customer_id text NOT NULL,
    full_name text,
    email text,
    phone text,
    segment text
);


-- Name: loans; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.loans (
    loan_id text NOT NULL,
    customer_id text,
    outstanding numeric,
    days_past_due integer,
    status text
);


-- Data for Name: customers; Type: TABLE DATA; Schema: public; Owner: -

COPY public.customers (customer_id, full_name, email, phone, segment) FROM stdin;
C1	Ann Lee	ann@mail.test	+1-555-0101	retail
C2	Bo Park	bo@mail.test	+1-555-0102	private
C3	Cy Diaz	cy@mail.test	+1-555-0103	retail
\.


-- Data for Name: loans; Type: TABLE DATA; Schema: public; Owner: -

COPY public.loans (loan_id, customer_id, outstanding, days_past_due, status) FROM stdin;
L1	C1	18500	45	delinquent
L2	C3	9200	10	current
\.


-- Name: customers customers_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.customers
    ADD CONSTRAINT customers_pkey PRIMARY KEY (customer_id);


-- Name: loans loans_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.loans
    ADD CONSTRAINT loans_pkey PRIMARY KEY (loan_id);


-- PostgreSQL database dump complete


-- ===== iam_db =====
\connect iam_db
-- PostgreSQL database dump






-- Name: user_access; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.user_access (
    user_id text,
    user_email text,
    system text,
    manager_email text
);


-- Data for Name: user_access; Type: TABLE DATA; Schema: public; Owner: -

COPY public.user_access (user_id, user_email, system, manager_email) FROM stdin;
u1	u1@bank.test	core	m1@bank.test
u2	u2@bank.test	swift	m1@bank.test
u3	u3@bank.test	core	m2@bank.test
\.


-- PostgreSQL database dump complete


-- ===== ops_db =====
\connect ops_db
-- PostgreSQL database dump






-- Name: audit_log; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.audit_log (
    id integer NOT NULL,
    entity text,
    entity_id text,
    action text,
    detail text,
    created_at timestamp with time zone DEFAULT now()
);


-- Name: audit_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -

CREATE SEQUENCE public.audit_log_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


-- Name: audit_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -

ALTER SEQUENCE public.audit_log_id_seq OWNED BY public.audit_log.id;


-- Name: kyc_requests; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.kyc_requests (
    id integer NOT NULL,
    customer_id text,
    email text,
    requested_on date,
    reminders_sent integer,
    status text
);


-- Name: kyc_requests_id_seq; Type: SEQUENCE; Schema: public; Owner: -

CREATE SEQUENCE public.kyc_requests_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


-- Name: kyc_requests_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -

ALTER SEQUENCE public.kyc_requests_id_seq OWNED BY public.kyc_requests.id;


-- Name: mortgage_applications; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.mortgage_applications (
    application_id text NOT NULL,
    applicant_email text,
    amount numeric,
    status text
);


-- Name: restructure_plans; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.restructure_plans (
    id integer NOT NULL,
    loan_id text,
    plan text,
    months integer,
    status text
);


-- Name: restructure_plans_id_seq; Type: SEQUENCE; Schema: public; Owner: -

CREATE SEQUENCE public.restructure_plans_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


-- Name: restructure_plans_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -

ALTER SEQUENCE public.restructure_plans_id_seq OWNED BY public.restructure_plans.id;


-- Name: audit_log id; Type: DEFAULT; Schema: public; Owner: -

ALTER TABLE ONLY public.audit_log ALTER COLUMN id SET DEFAULT nextval('public.audit_log_id_seq'::regclass);


-- Name: kyc_requests id; Type: DEFAULT; Schema: public; Owner: -

ALTER TABLE ONLY public.kyc_requests ALTER COLUMN id SET DEFAULT nextval('public.kyc_requests_id_seq'::regclass);


-- Name: restructure_plans id; Type: DEFAULT; Schema: public; Owner: -

ALTER TABLE ONLY public.restructure_plans ALTER COLUMN id SET DEFAULT nextval('public.restructure_plans_id_seq'::regclass);


-- Data for Name: audit_log; Type: TABLE DATA; Schema: public; Owner: -

COPY public.audit_log (id, entity, entity_id, action, detail, created_at) FROM stdin;
\.


-- Data for Name: kyc_requests; Type: TABLE DATA; Schema: public; Owner: -

COPY public.kyc_requests (id, customer_id, email, requested_on, reminders_sent, status) FROM stdin;
\.


-- Data for Name: mortgage_applications; Type: TABLE DATA; Schema: public; Owner: -

COPY public.mortgage_applications (application_id, applicant_email, amount, status) FROM stdin;
\.


-- Data for Name: restructure_plans; Type: TABLE DATA; Schema: public; Owner: -

COPY public.restructure_plans (id, loan_id, plan, months, status) FROM stdin;
1	L1		0	declined
\.


-- Name: audit_log_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -

SELECT pg_catalog.setval('public.audit_log_id_seq', 1, false);


-- Name: kyc_requests_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -

SELECT pg_catalog.setval('public.kyc_requests_id_seq', 1, false);


-- Name: restructure_plans_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -

SELECT pg_catalog.setval('public.restructure_plans_id_seq', 1, true);


-- Name: audit_log audit_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.audit_log
    ADD CONSTRAINT audit_log_pkey PRIMARY KEY (id);


-- Name: kyc_requests kyc_requests_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.kyc_requests
    ADD CONSTRAINT kyc_requests_pkey PRIMARY KEY (id);


-- Name: mortgage_applications mortgage_applications_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.mortgage_applications
    ADD CONSTRAINT mortgage_applications_pkey PRIMARY KEY (application_id);


-- Name: restructure_plans restructure_plans_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.restructure_plans
    ADD CONSTRAINT restructure_plans_pkey PRIMARY KEY (id);


-- PostgreSQL database dump complete


