# ocr_engine/config.py
import os
import boto3
import google.generativeai as genai
from dotenv import load_dotenv
from google.generativeai import types
import threading
import redis # For distributing keys across processes
from typing import Optional

# Load environment variables
load_dotenv()

# Base Directory - Less relevant for Celery worker's internal logic
# BASE_DIR = os.path.dirname(os.path.abspath(os.path.join(__file__, os.pardir)))
# PDF_UPLOAD_DIR = os.path.join(BASE_DIR, "pdf_uploads")
# NEWS_OUTPUT_DIR = os.path.join(BASE_DIR, "news_articles")
# TEMP_DIR = os.path.join(BASE_DIR, "temp")
# for dir_path in [PDF_UPLOAD_DIR, NEWS_OUTPUT_DIR, TEMP_DIR]:
#     os.makedirs(dir_path, exist_ok=True)

POPPLER_PATH = os.getenv("POPPLER_PATH", None)
if POPPLER_PATH and os.path.exists(POPPLER_PATH):
    print(f"OCR Engine Config: Using custom POPPLER_PATH: {POPPLER_PATH}")
elif POPPLER_PATH:
    print(f"⚠️ WARNING (OCR Engine Config): Custom POPPLER_PATH '{POPPLER_PATH}' set but does not exist.")
else:
    print(f"OCR Engine Config: POPPLER_PATH not set. pdf2image will search system PATH.")

SEGMENTATION_API_KEY = os.getenv("NEWSPAPER_SEGMENTATION_API_KEY", "")
if not SEGMENTATION_API_KEY:
    print("⚠️ WARNING (OCR Engine Config): NEWSPAPER_SEGMENTATION_API_KEY not set.")
else:
    print(f"Using Segmentation API key: ...{SEGMENTATION_API_KEY[-4:] if SEGMENTATION_API_KEY and len(SEGMENTATION_API_KEY) >=4 else 'N/A'}")

# --- Gemini API Key Management ---
GEMINI_API_KEYS_ENV = [
    os.getenv("GEMINI_API_KEY_1"), os.getenv("GEMINI_API_KEY_2"),
    os.getenv("GEMINI_API_KEY_3"), os.getenv("GEMINI_API_KEY_4"),
]
GEMINI_API_KEYS = [key for key in GEMINI_API_KEYS_ENV if key]

if not GEMINI_API_KEYS:
    print("⚠️ WARNING (OCR Engine Config): No Gemini API keys found (GEMINI_API_KEY_1 to _4). Gemini features will fail.")
else:
    print(f"Loaded {len(GEMINI_API_KEYS)} Gemini API keys for distribution.")

PROCESS_SPECIFIC_GEMINI_KEY = None # Stores the key for the current process
REDIS_HOST = os.getenv("REDIS_HOST", "redis") # Docker service name for Redis
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
REDIS_DB_FOR_KEYS = int(os.getenv("REDIS_DB_FOR_KEYS", 1)) # Use a different DB to avoid collision with Celery's main DB if needed
REDIS_KEY_COUNTER_NAME = "celery_worker_ML_key_idx_v2" # Unique counter name
_redis_key_client_for_assignment = None



def assign_gemini_key_and_configure_sdk():
    global PROCESS_SPECIFIC_GEMINI_KEY, _redis_key_client_for_assignment
    pid = os.getpid()
    if not GEMINI_API_KEYS:
        print(f"Process {pid}: No Gemini API keys available for assignment. SDK not configured.")
        return False

    assigned_key = None
    try:
        if _redis_key_client_for_assignment is None:
            _redis_key_client_for_assignment = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB_FOR_KEYS, decode_responses=False)
        _redis_key_client_for_assignment.ping()
        current_redis_index = _redis_key_client_for_assignment.incr(REDIS_KEY_COUNTER_NAME)
        key_list_index = (int(current_redis_index) - 1) % len(GEMINI_API_KEYS)
        assigned_key = GEMINI_API_KEYS[key_list_index]
        # print(f"Process {pid}: Assigned Gemini key ending ...{assigned_key[-4:]} (via Redis index {current_redis_index}, list index {key_list_index}).")
    except redis.exceptions.ConnectionError as e_redis:
        print(f"Process {pid}: Redis connection error for key assignment ({e_redis}). Falling back to PID-based key selection.")
        idx = pid % len(GEMINI_API_KEYS)
        assigned_key = GEMINI_API_KEYS[idx]
    except Exception as e_assign:
        print(f"Process {pid}: Error during Redis key index retrieval ({e_assign}). Using PID-based fallback.")
        idx = pid % len(GEMINI_API_KEYS)
        assigned_key = GEMINI_API_KEYS[idx]

    PROCESS_SPECIFIC_GEMINI_KEY = assigned_key
    try:
        genai.configure(api_key=PROCESS_SPECIFIC_GEMINI_KEY)
        print(f"Worker process {pid} CONFIGURED Gemini with key ending ...{PROCESS_SPECIFIC_GEMINI_KEY[-4:] if PROCESS_SPECIFIC_GEMINI_KEY and len(PROCESS_SPECIFIC_GEMINI_KEY) >=4 else 'N/A'}")
        return True
    except Exception as e_conf:
        key_display = f"...{PROCESS_SPECIFIC_GEMINI_KEY[-4:]}" if PROCESS_SPECIFIC_GEMINI_KEY and len(PROCESS_SPECIFIC_GEMINI_KEY) >=4 else "N/A"
        print(f"Worker process {pid} FAILED to configure Gemini with key {key_display}. Error: {e_conf}")
        PROCESS_SPECIFIC_GEMINI_KEY = None
        return False

# --- Gemini Model Definitions & Instances ---
# Validate model names - "gemini-2.0-flash" might not be a standard public model.
# Common choices: "gemini-1.5-flash-latest" (or "gemini-1.5-flash"), "gemini-1.5-pro-latest"
CONTENT_ANALYSIS_MODEL_NAME = os.getenv("GEMINI_CONTENT_MODEL", "gemini-2.5-flash")
CONTENT_ANALYSIS_GENERATION_CONFIG = types.GenerationConfig(
    candidate_count=1, stop_sequences=[], max_output_tokens=4096
)
CONTENT_ANALYSIS_SYSTEM_INSTRUCTION = """ You are a highly skilled Journalist working the Government of India. You are provided with the full text of a newspaper article. Perform the following tasks:

    1. **Language Detection:** Identify the article's original language.

    2. **Translation:** If the language is not English, translate the heading and content into English.

    3. **Date Extraction:** Extract the publication date if clearly visible in the text. Return it in dd-mm-yyyy format; otherwise use "unknown".

    4. **Summarization:** Provide a concise 2-3 sentence summary of the translated (or original English) article content.

    5. **Sentiment Analysis:** Determine the overall sentiment of the translated (or original English) article toward India and its government. Use the detailed classification rules below. Respond with ONLY ONE WORD: 'positive', 'negative', or 'neutral'.

        Sentiment Classification Rules:
        - If the content is political (about the Indian government, its leaders, or policies), estimate sentiment based on whether it highlights actions or decisions that benefit or harm the Indian government or its stability/performance.
        - If non-political, classify sentiment according to effects on India's national well-being, safety, prosperity, or international image.
        - If the content mentions effective action by the Indian government or security forces to protect the country or resolve threats, classify as Positive.
        - Achievements, milestones, or positive contributions → Positive.
        - Harm to India's environment, economy, stability, reputation → Negative.
        - Scandals or accusations damaging democratic trust → Negative.
        - A negative event offset by strong government response → Neutral.
        - Highlighting India as a leader in innovation, defense, cooperation, social progress → Positive.

        Detailed Sentiment Logic:
          Positive = showcases India/its government in a favorable light.
          Negative = focuses on damage, harm, instability, or anything that negatively impacts India or its image.
          Neutral  = impact is mixed or minimal, or negatives are countered by effective action.

    6. **Ministry Analysis:**

    ### Ministry List (choose from these exact names)
      - Ministry of Agriculture and Farmers' Welfare
      - Ministry of Animal Husbandry Dairying and Fisheries
      - Ministry of AYUSH
      - Ministry of Chemicals and Fertilizers
      - Ministry of Civil Aviation
      - Ministry of Coal
      - Ministry of Commerce and Industry
      - Ministry of Communications
      - Ministry of Consumer Affairs Food and Public Distribution System
      - Ministry of Cooperation
      - Ministry of Corporate Affairs
      - Ministry of Culture
      - Ministry of Defence
      - Ministry of Development of North Eastern Region
      - Ministry of Earth Sciences
      - Ministry of Education
      - Ministry of Electronics and Information Technology
      - Ministry of Environment Forest and Climate Change
      - Ministry of Finance
      - Ministry of Food Processing Industries
      - Ministry of Health and Family Welfare
      - Ministry of Heavy Industries
      - Ministry of Home Affairs
      - Ministry of Housing and Urban Affairs
      - Ministry of Information and Broadcasting
      - Ministry of Jal Shakti
      - Ministry of Labour and Employment
      - Ministry of Law and Justice
      - Ministry of Micro Small and Medium Enterprises
      - Ministry of Mines
      - Ministry of Minority Affairs
      - Ministry of New and Renewable Energy
      - Ministry of Panchayati Raj
      - Ministry of Parliamentary Affairs
      - Ministry of Personnel Public Grievances and Pensions
      - Ministry of Petroleum and Natural Gas
      - Ministry of Ports Shipping and Waterways
      - Ministry of Power
      - Ministry of Railways
      - Ministry of Road Transport and Highways
      - Ministry of Rural Development
      - Ministry of Science and Technology
      - Ministry of Skill Development and Entrepreneurship
      - Ministry of Social Justice and Empowerment
      - Ministry of Statistics and Programme Implementation
      - Ministry of Steel
      - Ministry of Textiles
      - Ministry of Tourism
      - Ministry of Tribal Affairs
      - Ministry of Women and Child Development
      - Ministry of Youth Affairs and Sports
      - Ministry of External Affairs
      - Prime Minister's Office
      - NITI Aayog

    ### GENERAL CLASSIFICATION RULES applicable for all ministries
    0. Do not assume anything on your own.
    1. Based on the news article, identify up to THREE Indian government ministries that are most relevant to the issues mentioned, chosen only from the list provided below. If fewer than three are relevant, return fewer; if none, return an empty list [].
    2. Use both contextual meaning and keywords to determine the ministry.
    3. Give priority to official schemes and departments.
    4. Your classification should reflect a deep understanding of:
    - Which ministries are **likely responsible or impacted**
    - Which policies, schemes, administrative roles, or governance functions are **core to the discussion**
    - **Who is being mentioned in what capacity**, and whether the **intent or outcome** aligns with a specific ministry’s domain.
    5. The news classified should be related to central government only.
    6. If the news is related to any private entity/entities where Indian Government is not related, discard it.
    7. Any article which falls under a broader category of a ministry should not be classified.
    8. News around bollywood, hollywood or any film industry **excluding legal cases against actors** where films are promoted or discussed as a hot topic should not be classified.
    9. If you come across any news article which is international and there is no direct/indirect relation to India/Indian Government, discard it.
    10. Classification should be made only if the article's primary focus, intent, or policy implications clearly fall within the scope of the ministry's responsibilities including its thematic domain, leadership role, or key initiatives.
    11. If the article only vaguely refers to a topic, mentions keywords incidentally, or does not clearly establish the ministry's relevance, then do not classify it under that ministry even if signal terms appear.
    12. It is not necessary to classify an article into any ministry if the available information is insufficient, vague, or off-topic. Return an empty "ministries" array in such cases.
    13. Classify the news to a ministry only if it is directly under the ministry interest.

    ### SPECIAL CLASSIFICATION RULES based on Meta Data provided for individual ministries:

    Use the **below special classification rules and reference lists** Treat the rules as the ONLY decision support for classification**
    This analysis is meant to simulate how a human expert would classify the article: based on **intent, relevance, responsibility, and administrative fit**, rather than just string-matching.

    1. If any of the key officials are mentioned in the article from the list `key_officials_list` for that ministry, treat them as a strong signal for classifying under that ministry.
    2. If any of the keywords/phrases appear (case-insensitive) in the article from the list `keywords_phrases_list` for that ministry, treat them as a strong signal to classify under that ministry.
    3. If any of the Policies/Schemes appear (case-insensitive) in the article from the list `Policies_schemes_list` for that ministry, treat them as a strong signal to classify under that ministry.
    4. If any of the Organizations appear (case-insensitive) in the article from the list `Organization_list` for that ministry, treat them as a strong signal to classify under that ministry.
    5. If any of the keywords/phrases appear (case-insensitive) in the article from the list `temporary_keywords` for that ministry, treat them as a strong signal to classify under that ministry.
    6. If `specific_rules` list is not empty, use the list items as additional rules to classify the news under that ministry. These rules will have more weightage than general rules.

    ## START OF META DATA FOR MINISTRIES ##

    {

"Ministry of Electronics and Information Technology": {
"key_officials_list": ["Ashwini Vaishnaw", "Jitin Prasada", "S. Krishnan", "Abhishek Singh", "Amitesh Kumar Sinha", "Rajesh Singh", "Sushil Pal", "Krishan Kumar Singh"],
"keywords_phrases_list": ["Chip Design","Semiconductor","MEITY","Digital India", "India Stack", "CoWIN", "MyGov", "DigiLocker", "Bhashini", "AI in governance", "India AI Mission", "DPI", "API Setu", "App Store India", "UMANG", "ONDC", "Common Services Centres", "Digital Village program", "chip design", "fabrication", "ATMP", "Chips to Startup", "Foxconn", "Applied Materials", "Lam Research", "e-KYC", "Aadhaar Face Authentication", "Aadhaar authentication", "AI for Good Governance", "National e-Governance Division", "eOffice", "eCabinet", "Foundations and Risk Mitigation in AI/ML", "AI Adoption for Enhanced Governance", "AI Tools for Smarter Public Administration", "Building Robust AI Infrastructure", "AI-related risks", "OpenForge", "National Cloud Services", "GI Cloud", "MeghRaj", "DIKSHA platform", "Government e-Marketplace", "eSanjeevani", "e-Hospital", "Techade", "National Supercomputing Mission", "India Innovation Centre for Graphene", "Global Value Chains", "Electronics Manufacturing Clusters", "Electronics Systems Design and Manufacturing", "ESDM sector", "IECT", "ICT sector", "IT Hardware manufacturing sector", "M-SIPS", "Viability Gap Funding", "BPO", "ITeS", "STPI", "EHTP", "Electronic Hardware Technology Park", "Ready Built Factory", "Plug and Play facilities", "Government-to-Citizen e-Services", "TIDE", "Technology Incubation and Development of Entrepreneurs"],
"Policies_schemes_list": ["Chips to Startup (C2S)", "Common Services Centres", "Digital Village program", "Technology Incubation and Development of Entrepreneurs (TIDE)", "AI for Good Governance", "Digital Infrastructure for Knowledge Sharing (DIKSHA)", "MeghRaj", "National Supercomputing Mission", "Electronics Manufacturing Clusters", "Electronics System Design and Manufacturing (ESDM)", "Modified Special Incentive Package Scheme (M-SIPS)", "Viability Gap Funding (VGF) for BPO/ITeS"],
"Organization_list": ["National e-Governance Division", "Software Technology Parks of India", "Electronic Hardware Technology Park", "Government e-Marketplace", "India Innovation Centre for Graphene", "OpenForge", "National Cloud Services", "GI Cloud", "MeghRaj"],
"temporary_keywords": ["NIXI","Dr. Devesh Tyagi", "77 internet exchange points"],
"specific_rules": ["Avoid news related to election commission.", "Article which contains generic news on technology should not be classified"]
},

"Prime Minister's Office": {
"key_officials_list": ["PM Modi", "Prime Minister Modi", "Narendra Modi", "Narendar Modi", "Modi", "PM", "PMO", "pmo", "Dr. P. K. Mishra", "Ajit Doval", "Shaktikanta Das", "Amit Khare", "Tarun Kapoor", "Vivek Kumar", "Hardik Satishchandra Shah", "Nidhi Tewari"],
"keywords_phrases_list": ["Prime Minister's Visit", "Bilateral Summit", "Modi", "Pradhan Mantri", "PM's Intervention", "PM's Statement", "PM's Message", "PM's Participation", "PM's Virtual Address", "PM's Bilateral Meetings", "PM's Interaction with Diaspora", "PMO Coordination", "PMO Oversight", "PMO-led Initiative", "Mann ki Baat", "PMO Monitoring", "PMO Review", "PMO Approval", "PMO Guidance", "PMO Briefing", "Modi 3.0", "PMO India"],
"Policies_schemes_list": ["Digital India", "Make in India", "Swachh Bharat", "Atmanirbhar Bharat", "Vasudhaiva Kutumbakam", "International Day of Yoga", "Voice of Global South", "PM Vishwakarma Yojana", "PM eBus Seva", "PM Poshan Shakti Nirman Abhiyaan", "PM SVANidhi", "PM Garib Kalyan Rojgar Abhiyaan", "PM Matsya Sampada Yojana", "PM Kisan Samman Nidhi", "PM Kisan Urja Suraksha Evam Utthan Mahabhiyan", "PM Shram Yogi Mandhan", "PM Annadata Aay Sanrakshan Abhiyan", "PM Jan Vikas Karyakaram", "PM Matritva Vandana Yojana", "PM Ujjwala Yojana", "PM Fasal Bima Yojana", "PM Krishi Sinchai Yojana", "PM Mudra Yojana", "PM Gramin Awas Yojana", "PM Awaas Yojana - (Urban)", "PM Suraksha Bima Yojana", "PM Kaushal Vikas Yojna", "PM Bhartiya Jan Aushadhi Kendra", "PM Jan Dhan Yojana", "PM Adarsh Gram Yojana"],
"temporary_keywords": ["11 Years", "Modi Government"],
"specific_rules": ["News which are related to India's  "]
},

"Ministry of Defence": {
"key_officials_list": ["Rajnath Singh", "Sanjay Seth", "Rajesh Kumar Singh"],
"keywords_phrases_list": ["Indian Army", "Indian Air Force", "Indian Navy", "integrated defence staff", "Chief of Defence Staff", "Northern Command", "Western Command", "Southern Command", "Eastern Command", "Central Command", "South Western Command", "Army Training Command", "Border Roads Organization", "Directorate General Defence Estates", "National Defence College", "National Cadets Corps", "Institute for Defence Studies and Analysis", "School of Foreign Language", "Armed Forces Tribunal", "Armed Forces Medical College", "Military Engineering Services", "College of Defence Management", "Defence Services Staff College", "Indian Coast Guard", "Services Sports Control Board", "Controller General of Defence Accounts", "NCC Cadets", "National Defence Academy", "Commanding-in-Chief", "Ati Vishisht Seva Medal", "Param Vishisht Seva Medal", "Uttam Yudh Seva Medal", "Sena Medal", "National War Memorial", "Military Nursing Service", "Operation Sindoor"],
"Policies_schemes_list": ["Agnipath Scheme", "Prime Minister's Scholarship Scheme (PMSS)", "Defence Testing Infrastructure Scheme (DTIS)", "Ex-Servicemen Welfare Schemes", "Army Surplus Vehicles to ESM/Widows", "National Defence Fund Scholarship", "Welfare Schemes of Kendriya Sainik Board (KSB)", "iDEX - Innovations for Defence Excellence", "Technology Development Fund (TDF)", "SRIJAN Portal"],
"Organization_list": ["Department of Defence (DoD)", "Department of Military Affairs (DMA)", "Department of Defence Production (DDP)", "Department of Defence Research and Development (DRDO)", "Department of Ex-Servicemen Welfare (DESW)", "Hindustan Aeronautics Limited (HAL)", "Bharat Electronics Limited (BEL)", "Bharat Dynamics Limited (BDL)", "BEML Limited (BEML)", "Mazagon Dock Shipbuilders Limited (MDL)", "Garden Reach Shipbuilders and Engineers Limited (GRSE)", "Mishra Dhatu Nigam Limited (MIDHANI)", "Armoured Vehicles Nigam Limited (AVNL)", "Advanced Weapons and Equipment India Limited (AWEIL)", "Munitions India Limited (MIL)", "Yantra India Limited (YIL)", "India Optel Limited (IOL)", "Troop Comforts Limited (TCL)", "Gliders India Limited (GIL)"],
"temporary_keywords": [],
"specific_rules": []
},

"Ministry of External Affairs": {
"key_officials_list": ["S. Jaishankar", "Kirti Vardhan Singh", "Pabitra Margherita", "Vikram Misri", "Tanmaya Lal", "Jaideep Mazumdar", "Randhir Jaiswal"],
"keywords_phrases_list": ["India's Neighbourhood", "Indian Ocean Region", "BIMSTEC", "SAARC", "G20", "Consular Services", "Passport Services", "Visa Services", "Overseas Indian Affairs", "New Emerging and Strategic Technologies", "Cyber Diplomacy", "Public Diplomacy", "SCO Summit", "Voice of Global South Summits", "India-CARICOM", "India-SICA", "ASEAN", "Plurilateral", "Multilateral", "Bilateral", "G20 Presidency", "Consensus Declaration", "Jan Bhagidari", "Vasudhaiva Kutumbakam", "SAGAR Policy", "Neighbourhood First Policy", "Strategic Partnerships", "High-impact Grant Projects", "Lines of Credit", "People-to-people Ties", "First Responder", "Disengagements", "Maritime Domain Awareness", "Global Biofuels Alliance", "Migration and Mobility Partnership", "Asian Development Bank", "Financial Stability Board", "IMF", "ILO", "WTO", "ISA", "CDRI", "OECD", "UNWFP", "ICCR", "e-Vidya Bharti Portal", "Passports Seva", "Rules-based International Order", "Global South", "Supply Chain Disruptions", "Disarmament", "Non-Proliferation", "Weapons of Mass Destruction", "Cyber Dialogues", "Track 1.5 Dialogue", "Special Envoy", "Troika", "Sherpa Track", "Strategic Dialogue", "Pravasi Bharatiya Divas", "Overseas Citizen of India", "Person of Indian Origin", "Defence Cooperation Agreement", "Joint Military Exercise", "Counter-terrorism Cooperation", "Maritime Security Dialogue", "Defence Attaché", "Peacekeeping Operations", "Military-to-Military Engagement", "Bilateral Investment Treaty", "Double Taxation Avoidance Agreement", "Preferential Trade Agreement", "Comprehensive Economic Partnership Agreement", "Market Access", "Tariff Concessions", "Trade Facilitation", "BRICS", "IBSA Dialogue Forum", "QUAD", "East Asia Summit", "ASEAN-India Summit", "Shanghai Cooperation Organisation", "G77", "SAARC Development Fund", "Extradition Treaty", "Repatriation"],
"Policies_schemes_list": ["Indian Community Welfare Fund (ICWF)", "Know India Programme (KIP)", "e-Migrate Portal", "Scholarship Programmes for Diaspora Children (SPDC)", "Mahatma Gandhi Pravasi Suraksha Yojana (MGPSY)", "Pravasi Bharatiya Bima Yojana (PBBY)", "Pravasi Bharatiya Divas", "SAGAR Policy", "Voice of Global South", "Migration and Mobility Partnership", "Comprehensive Economic Partnership Agreement (CEPA)", "Double Taxation Avoidance Agreement (DTAA)", "Bilateral Investment Treaty (BIT)"],
"Organization_list": ["Indian Council for Cultural Relations (ICCR)", "International Solar Alliance (ISA)", "Coalition for Disaster Resilient Infrastructure (CDRI)", "Asian Development Bank (ADB)", "World Trade Organization (WTO)", "International Monetary Fund (IMF)", "Organisation for Economic Co-operation and Development (OECD)", "United Nations World Food Programme (UNWFP)"],
"temporary_keywords": [],
"specific_rules": []
},

"Ministry of Finance": {
"key_officials_list": ["Nirmala Sitharaman", "Ajay Seth", "Pankaj Chaudhary", "Vumlunmang Vualnam", "Arunish Chawla", "Nagaraju Maddirala", "K. Moses Chala", "Arvind Shrivastava", "V. Anantha Nageswaran"],
"keywords_phrases_list": ["Union Budget", "Fiscal Deficit", "Revenue Deficit", "Effective Revenue Deficit", "CapEx", "RE", "BE", "Budget Estimates", "Revised Estimates", "Gross Market Borrowings", "Public Debt", "Disinvestment", "Strategic Disinvestment", "Debt Sustainability", "Public Account of India", "Consolidated Fund of India", "Contingency Fund", "Outcome Budget", "MTEF", "Appropriation Bill", "Finance Bill", "Vote on Account", "Token Grant", "Budget Call Letter", "Budget Circular", "Zero-Based Budgeting", "Performance-Based Budgeting", "Outcome-Based Monitoring", "Budget Transparency", "Demand Aggregation", "Modified Cash Basis of Accounting", "Warrant Authority System", "Audit Observations", "Interest Subvention", "Digital Rupee", "CBDC", "Unified Payments Interface", "UPI", "Direct Benefit Transfer", "DBT", "Jan Dhan", "JAM Trinity", "SEZ", "FRBM Act", "GST Council", "FATF", "FSAP", "FSDC", "IFSC", "PFMS", "NIP", "NIIF", "DIPAM", "GeM", "Debt Sustainability Analysis", "Fiscal Slippage", "Public-Private Partnership", "Viability Gap Funding", "India Investment Grid", "Sovereign Green Bonds", "Social Bonds", "Green Securitization", "Outcome Budget", "Inclusive Development Index", "BEPS", "APA", "MAT", "TDS", "STT", "TCS", "Income Tax Settlement Commission", "Liquidity Adjustment Facility", "Statutory Liquidity Ratio", "Interest Liability", "Monetary-Fiscal Interface", "Devolution of Taxes", "Fiscal Consolidation Roadmap", "Deficit Financing", "External Commercial Borrowings", "LAF", "SLR", "Cash Management System", "Consolidated Sinking Fund", "Market Stabilization Scheme"],
"Policies_schemes_list": ["Stand Up India", "Pradhan Mantri Garib Kalyan Yojana (PMGKY)", "Aam Admi Bima Yojana", "Pradhan Mantri Suraksha Bima Yojana", "Pradhan Mantri Jeevan Jyoti Bima Yojana (PMJJBY)", "Atal Pension Yojana", "National Pension Scheme (NPS)", "Pradhan Mantri Vaya Vandana Yojana (PMVVY)", "Pradhan Mantri MUDRA Yojana", "Pradhan Mantri Jan Dhan Yojana", "Financial Sector Assessment Programme (FSAP)", "Credit Guarantee Scheme", "Interest Subvention Scheme", "Anusandhan National Research Fund", "Climate Finance Taxonomy", "Sustainable Securitized Debt Instruments", "Equalisation Levy", "E-invoicing System (GST)", "Counter-Cyclical Fiscal Policy", "Tax Expenditure Statement", "Off-Budget Borrowings", "Monetized Deficit"],
"Organization_list": ["Department of Economic Affairs (DEA)", "Department of Expenditure (DoE)", "Department of Financial Services (DoFS)", "Department of Investment and Public Asset Management (DIPAM)", "Department of Revenue (DoR)", "Department of Public Enterprises (DPE)", "Reserve Bank of India (RBI)", "Central Board of Direct Taxes (CBDT)", "Central Board of Indirect Taxes and Customs (CBIC)", "Securities and Exchange Board of India (SEBI)", "Pension Fund Regulatory and Development Authority (PFRDA)", "Insurance Regulatory and Development Authority of India (IRDAI)", "Financial Stability and Development Council (FSDC)", "Financial Intelligence Unit - India (FIU-IND)", "Central Economic Intelligence Bureau (CEIB)", "Controller General of Accounts (CGA)", "National Investment and Infrastructure Fund (NIIF)", "Public Financial Management System (PFMS)", "National Financial Reporting Authority (NFRA)"],
"temporary_keywords": [],
"specific_rules": []
},

"Ministry of Information and Broadcasting": {
"key_officials_list": ["Shri Ashwini Vaishnaw", "Dr. L Murugan", "Shri Sanjay Jaju"],
"keywords_phrases_list": ["I&B","Cable Television Networks (Regulation) Act 1995", "Cinematograph Act 1952", "Press and Registration of Periodicals Act 2023", "Self-regulatory Bodies", "Content Regulation", "Media Ethics", "Media Accreditation", "Fact Checking Unit (FCU)", "Programme Code", "Advertising Code", "Emergency Alert Dissemination", "Community Radio Guidelines", "Digital Media Ethics Code", "OTT (Over-the-top) Regularization", "Broadcasting Infrastructure and Network Development (BIND) Scheme", "Community Radio Station (CRS)", "Vartalap", "Azadi Ka Amrit Mahotsav", "Mann Ki Baat", "Yuva Sangam", "MIB – Ministry of Information and Broadcasting", "CBC – Central Bureau of Communication", "PIB – Press Information Bureau", "NFDC – National Film Development Corporation", "DFF – Directorate of Film Festivals", "CBFC – Central Board of Film Certification", "BECIL – Broadcast Engineering Consultants India Ltd", "FTII – Film and Television Institute of India", "SRFTI – Satyajit Ray Film and Television Institute", "IIMC – Indian Institute of Mass Communication", "EMMC – Electronic Media Monitoring Centre", "CRS – Community Radio Station", "DTH – Direct to Home", "DRM – Digital Radio Mondiale", "BIND – Broadcasting Infrastructure and Network Development", "IRD – Integrated Receiver Decoder", "DSNG – Digital Satellite News Gathering", "National Channel", "Jan Vishwas Act 2023", "E-Cinepramaan", "Cinematograph (Certification) Rules 2024", "National Film Heritage Mission (NFHM)", "SHABD Initiative", "Cinematograph (Amendment) Act 2023", "Press and Registration of Periodicals Act 2023 (PRP Act)"],
"Policies_schemes_list": ["Development Communication & Information Dissemination (DCID)", "Development Communication & Dissemination of Filmic Content (DCDFC)", "Broadcasting Infrastructure Network Development (BIND)", "Supporting Community Radio Movement in India"],
"Organization_list": ["Press Information Bureau", "Central Bureau Of Communication", "Press Registrar General of India", "Directorate of Publication Division (DPD)", "New Media Wing", "Electronic Media Monitoring Centre (EMMC)", "Central Board of Film Certification", "Press Council of India", "Prasar Bharati", "Indian Institute of Mass Communication"],
"temporary_keywords": ["#BadaltaBharatMeraAnubhav","Viksit Bharat@2047", "Badalta Bharat Mera Anubhav"  ],
"specific_rules": ["No bollywood or film industry news article to be categorized", "legal matters should be classified", "Article related to TV Programs, Movies, Music Programs, Concerts and New release should not be categorized"]
},

"Ministry of Civil Aviation": {
"key_officials_list": ["Kinjarapu Ram Mohan Naidu", "General V. K. Singh", "Vumlunmang Vualnam"],
"keywords_phrases_list": ["UDAN", "airport development", "regional air connectivity", "DGCA", "Air India", "Vistara", "IndiGo", "SpiceJet", "flight safety norms", "air traffic control", "aviation sector growth", "AAI", "drone regulations", "airfare caps", "airline privatization", "pilot licensing", "civil aviation policy"],
"Policies_schemes_list": ["UDAN (Ude Desh ka Aam Naagrik)", "National Civil Aviation Policy", "Drone Rules 2021", "DigiYatra initiative", "AirSewa grievance redressal portal"],
"Organization_list": ["Directorate General of Civil Aviation", "DGCA", "Bureau of Civil Aviation Security", "BCAS", "Airport Authority of India", "AAI", "Airports Economic Regulatory Authority", "AERA", "Pawan Hans Limited", "Air India Asset Holding Ltd"],
"temporary_keywords": ["Air India", "AI 171", "Ahmedabad to London", "Plane Crash"],
"specific_rules": []
},

"Ministry of Home Affairs": {
"key_officials_list": ["Amit Shah", "Nityanand Rai", "Bandi Sanjay Kumar", "Govind Mohan"],
"keywords_phrases_list": ["Unlawful Activities (Prevention) Act (UAPA)", "Left Wing Extremism (LWE)", "Jammu & Kashmir Reorganisation", "Good Governance Index (GGI)", "District Good Governance Index (DGGI)", "Population Register", "Freedom Fighters Pension Schemes", "Padma Awards Secretariat", "Model Police Act", "Radicalization Monitoring", "BHARATIYA NYAYA SANHITA", "BHARATIYA NAGARIK SURAKSHA SANHITA", "BHARATIYA SAKSHYA ADHINIYAM", "Centre-State Relations", "Union Territories", "Empowered Committee on Border Infrastructure (ECBI)", "Integrated Check Post (ICP)", "National Information Security Policy and Guidelines (NISPG)", "Coastal Security Schemes (CSS)", "Border Out Posts", "Human Rights", "National Integration", "Communal Harmony", "Rehabilitation of Migrants", "Enemy Property", "Gallantry Awards", "De-radicalization", "Extradition", "Cross Border Firing", "Improvised Explosive Device (IED)", "Insurgency", "Repatriation", "Security Clearance", "Inter-State Boundary Disputes", "Anti-Naxal Operations", "Counter-Insurgency (COIN)", "Special Police Units", "Terror Financing", "Intelligence Sharing Mechanisms", "Ballistic Analysis", "DNA Profiling", "Cyber Security Awareness Campaigns", "Phishing and Malware Attacks", "Integrated Border Management System (IBMS)", "Border Area Development Council (BADC)", "Cross-Border Smuggling", "Illegal Immigration Control", "Visa and Immigration Policies", "Bilateral Security Agreements", "Joint Border Patrols", "Transnational Crime", "Maritime Security", "Coastal Surveillance Network", "International Border Fencing", "Border Infrastructure Development", "Customs and Excise Coordination", "Communal Violence Prevention", "Anti-Human Trafficking Measures", "Inter-Agency Task Force", "Civil-Military Coordination", "Critical Incident Management", "Security Clearance Protocols", "Narcotics Control", "Intelligence Fusion Centres", "Firearms Licensing", "Explosive Ordnance Disposal (EOD)", "Anti-Smuggling Operations", "Cordon and Search Operations"],
"Policies_schemes_list": ["Scheme of Modernization of Prisons", "Swatantrata Sainik Samman Pension Scheme", "Kabir Puraskar Scheme", "Central Scheme for Assistance toward damaged Immovable/Movable Property During Action by CPMFs AND ARMY in Jammu & Kashmir", "Resettlement of Bru migrants", "Scheme for Surrender-cum-Rehabilitation of insurgents in NE States", "Scheme for providing relief and rehabilitation assistance to Sri-Lankan refugees in the refugee camps", "Vibrant Villages Programme", "Disaster Management Schemes", "Police Modernization Scheme", "Schemes for Left Wing Extremism (LWE) Affected Areas", "Border Area Development Programme (BADP)", "CAPF Welfare Schemes"],
"Organization_list": ["National Investigation Agency (NIA)", "Central Armed Police Forces (CAPFs)", "Cyber Crime Coordination Centre (I4C)", "National Intelligence Grid (NATGRID)", "Intelligence Bureau (IB)", "National Security Guard (NSG)", "Inter-State Council Secretariat", "Registrar General & Census Commissioner", "National Crime Records Bureau (NCRB)", "Central Forensic Science Laboratory (CFSL)", "Sashastra Seema Bal (SSB)", "Border Security Force (BSF)", "Indo-Tibetan Border Police (ITBP)", "Central Reserve Police Force (CRPF)", "Rapid Action Force (RAF)", "Women Safety Division", "Disaster Management Division", "Forensic Science Laboratories (FSLs)", "Police Training Institutes"],
"temporary_keywords": [],
"specific_rules": ["Extract only those news articles that are relevant to the Ministry of Home Affairs (MHA) at the central level. Focus on topics such as national security, terrorism, border management, NIA, CBI, UAPA, citizenship (NRC/CAA), cyber security (handled by MHA)","Exclude small regional/local crime stories, general state police actions, local thefts, assaults, or law-and-order issues that do not involve central agencies or policy-level implications","News retaled to Centre-State Relations should not be classified even if it directly relates to ministry interest","News related to cyber crime and digital fraud should not be categorized"]
},

"Ministry of Road Transport & Highways": {
"key_officials_list": ["Nitin Gadkari", "Ajay Tamta", "Harsh Malhotra", "Shri V. Umashankar"],
"keywords_phrases_list": ["Motor Vehicles Act, 1988", "Central Motor Vehicles Rules, 1989", "National Highways Act, 1956", "National Highways Fee (Determination of Rates and Collection) Rules, 2008", "Road Transport Corporations Act, 1950", "Carriage by Road Act, 2007", "Carriage by Road Rules, 2011", "MoRTH – Ministry of Road Transport & Highways", "IRC – Indian Roads Congress", "IHMCL – Indian Highways Management Company Ltd.", "TRW – Transport Research Wing", "SRTUs – State Road Transport Undertakings", "PIU/PD Offices – Project Implementation Unit / Project Director", "Parvatmala Pariyojana", "Vision 2047", "Bharat New Car Assessment Program (BNCAP)", "Vehicle Scrapping Policy", "eTransport Project", "BhoomiRashi Portal", "e-DAR", "iRAD", "MMLP", "Humsafar Policy", "Model Concession Agreement (MCA)", "BOT (Toll)", "EPC Projects", "VAHAN and SARATHI", "PM GatiShakti", "Expressways / High-Speed Corridors", "NH – National Highway", "SH – State Highway", "Greenfield/Brownfield Projects", "SPV – Special Purpose Vehicle", "Bharatmala Pariyojana", "National Highway Development Project (NHDP)", "Special Accelerated Road Development Programme for North-East (SARDP-NE)", "Economic Importance & Interstate Connectivity (EI&ISC)", "Toll Operate Transfer (TOT)", "Infrastructure Investment Trust (InvIT)", "FASTag", "National Electronic Toll Collection (NETC)", "Electronic Toll Collection (ETC)", "HSC (High-Speed Corridor)", "OMT (Operate, Maintain, Transfer)", "BOT (Build, Operate, Transfer)", "Delhi–Mumbai Expressway", "Amritsar–Jamnagar Corridor", "Kanpur–Lucknow Expressway", "Raipur–Visakhapatnam Corridor", "Hyderabad–Visakhapatnam Corridor", "Surat–Solapur Corridor", "Varanasi–Ranchi–Kolkata Corridor", "Ayodhya Ring Road", "Nashik Phata–Khed Corridor"],
"Policies_schemes_list": ["Cashless Treatment Scheme", "Rah-Veer (Good Samaritan) Scheme", "Road Safety Advocacy Scheme", "National Highways Accident Relief Service Scheme (NHARSS)", "Refresher Training for Heavy Vehicle Driver"],
"Organization_list": ["National Highways Authority of India (NHAI)", "National Highways and Infrastructure Development Corporation Limited (NHIDCL)", "Central Road Research Institute (CRRI)", "Indian Academy of Highway Engineers (IAHE)"],
"temporary_keywords": [],
"specific_rules": []
},

"Ministry of Railways": {
"key_officials_list": ["Shri Ashwini Vaishnaw", "Shri V. Somanna", "Shri Ravneet Singh"],
"keywords_phrases_list": ["Central Railway", "Eastern Railway", "East Central Railway", "East Coast Railway", "Northern Railway", "North Central Railway", "North Eastern Railway", "North Frontier Railway", "North Western Railway", "Southern Railway", "South Central Railway", "South Eastern Railway", "South East Central Railway", "South Western Railway", "Western Railway", "West Central Railway", "Metro Railway, Kolkata", "South Coast Railway", "Integral Coach Factory (ICF), Chennai", "Rail Coach Factory (RCF), Kapurthala", "Modern Coach Factory (MCF), Rae Bareli", "Diesel Locomotive Works (DLW), Varanasi", "Chittaranjan Locomotive Works (CLW), West Bengal", "Diesel-Loco Modernisation Works (DMW), Patiala", "Rail Wheel Factory (RWF), Bangalore", "Rail Wheel Plant, Bela", "Kavach – Train Collision Avoidance System", "Vande Bharat Express", "UDAY Express", "Bio-Toilets in Trains", "Electrification of Railway Lines", "Semi-High-Speed Corridors", "High-Speed Rail", "Zonal Railways", "Indian Railways (IR)", "Passenger Reservation System (PRS)", "Unreserved Ticketing System (UTS)", "Freight Operations Information System (FOIS)", "Dedicated Freight Corridor (DFC)", "Mission Raftaar", "One Station One Product (OSOP)", "Amrit Bharat Trains", "Hydrogen Train-set", "Vande Bharat Sleeper", "Vande Metro", "Railway Board", "MoR – Ministry of Railways"],
"Policies_schemes_list": ["Amrit Bharat Station Scheme", "Vikalp Scheme", "Rail Kaushal Vikas Yojana", "Project Saksham"],
"Organization_list": ["Braithwaite and Co Limited", "Central Organisation for Modernisation of Workshops (COFMOW)", "Centre for Railway Information Systems (CRIS)", "Container Corporation of India Limited (CONCOR)", "Dedicated Freight Corridor Corporation of India (DFCCIL)", "IRCON International Limited", "Indian Railway Catering and Tourism Corporation Ltd. (IRCTC)", "Indian Railway Finance Corporation Limited (IRFC)", "Integral Coach Factory, Chennai", "Konkan Railway Corporation Limited", "Kutch Railway Company Limited, Delhi", "Mumbai Railway Vikas Corporation Limited (MRVC)", "Pipavav Railway Corporation Limited", "Rail India Technical and Economic Service Limited (RITES)", "Rail Vikas Nigam Limited", "RailTel Corporation of India Limited", "Research Designs and Standards Organisation (RDSO), Lucknow", "Railway Protection Force (RPF)", "Railway Recruitment Boards (RRBs)", "Railway Recruitment Cells (RRCs)"],
"temporary_keywords": [],
"specific_rules": []
},

"Ministry of Minority Affairs": {
"key_officials_list": ["Shri Kiren Rijiju", "Shri George Kurian", "Dr. Chandra Shekhar Kumar", "Shri C.P.S. Bakshi", "Shri Ankur Yadav", "Shri Md. Nadeem"],
"keywords_phrases_list": ["Minorities", "Minority Welfare", "Educational Empowerment of Minorities", "Skill Development of Minorities", "Madrasa", "All India Muslim Personal Law Board", "Prime Minister’s New 15 Point Programme", "Inclusive Development", "Minority Communities", "Constitutionally recognized minorities: Muslims, Christians, Sikhs, Buddhists, Parsis, Jains", "Haj Pilgrims", "Waqf", "Waqf Board", "Waqf Properties", "Waqf Amendment Act", "Lok Samvardhan Parv"],
"Policies_schemes_list": ["Nai Manzil", "Nai Roshni", "Seekho aur Kamao", "USTTAD", "Hamari Dharohar", "Scholarships for Minorities", "Pre-matric Scholarship for Minorities", "Post-matric Scholarship for Minorities", "Merit-cum-Means Scholarship for Minorities", "PMJVK (Pradhan Mantri Jan Vikas Karyakram)", "Jiyo Parsi", "Haj Suvidha App"],
"Organization_list": ["Central Waqf Council", "Maulana Azad Education Foundation (MAEF)", "National Commission for Minorities (NCM)", "National Minorities Development and Finance Corporation (NMDFC)"],
"temporary_keywords": [],
"specific_rules": []
},

"Ministry of Women and Child Development": {
"key_officials_list": ["Annpurna Devi", "Savitri Thakur"],
"keywords_phrases_list": ["BETI BACHAO BETI PADHAO", "BBBP", "RASHTRIYA POSHAN MAAH", "POSHAN ABHIYAN", "POSHAN TRACKER", "POSHAN PAKHWADA", "POSHAN BHI PADHAI BHI", "POSHAN VATIKA", "VEER BAAL DIWAS", "PMRBP", "PRADHAN MANTRI RASHTRIYA BAL PURASKAR", "ICDS", "INTEGRATED CHILD DEVELOPMENT SERVICES", "SUPOSHIT GRAM PANCHAYAT ABHIYAN", "NATIONAL GIRL CHILD DAY", "ANGANWADI WORKERS", "ANGANWADI HELPERS", "ANGANWADI CENTRES", "GENDER JUSTICE", "MMR", "MATERNITY MORTALITY RATIO", "IMR", "INFANT MORTALITY RATIO", "PRADHAN MANTRI SURAKSHIT MATRITVA ABHIYAN", "HEALTH AND WELLNESS CENTRE", "PRADHAN MANTRI MATRU VANDANA YOJANA", "MISSION VATSALYA", "NCPCR", "NATIONAL COMMISSION FOR PROTECTION OF CHILD RIGHTS", "NCW", "NATIONAL COMMISSION FOR WOMEN", "NARI SHAKTI", "VIKSIT BHARAT", "INTERNATIONAL WOMEN'S DAY", "SAKSHAM ANGANWADI POSHAN 2.0", "MISSION SHAKTI", "PALNA SCHEME", "SWACHHATA", "UNICEF", "UNITED NATIONS CHILDREN'S FUND", "NUTRITION", "INTERNATIONAL DAUGHTER'S DAY", "SUKANYA SAMRIDDHI YOJANA", "KUPOSHAN MUKT BHARAT", "SHE-BOX PORTAL", "INTERNATIONAL DAY OF THE GIRL CHILD", "OSC", "ONE STOP CENTRE", "BAL VIVAH MUKT BHARAT", "AWCC", "ANGANWADI CUM CRECHE CENTRE", "CNCP", "CHILDREN IN NEED OF CARE AND PROTECTION", "CHILDREN IN CONFLICT WITH LAW", "CCL", "PM POSHAN", "PRADHAN MANTRI POSHAN SHAKTI NIRMAN", "THE SEXUAL HARASSMENT OF WOMEN AT WORKPLACE ACT 2013", "SH ACT", "SHE BUILDS BHARAT", "CHINTAN SHIVIR", "GENDER BUDGET ALLOCATION", "WOMEN EMPOWERMENT", "NIRBHAYA FUND", "NARI SHAKTI SE VIKSIT BHARAT", "POLITICAL PARTICIPATION", "LOCAL GOVERNANCE", "UNCSW", "UNITED NATIONS CONVENTION ON THE STATUS OF WOMEN", "GRASSROOTS WOMEN LEADERS", "HOLISTIC DEVELOPMENT OF NORTHEAST INDIA", "CHILD ADOPTIONS", "POCSO", "PROTECTION OF CHILDREN FROM SEXUAL OFFENCES ACT", "CHILD OBISITY", "WOMEN AND CHILD DEVELOPMENT", "SAMBAL", "SAMARTHYA", "MALNUTRITION", "ANAEMIA", "STUNTING", "WASTING", "NARI ADALAT", "WOMEN HELP LINE", "WORKING WOMEN HOSTEL"],
"Policies_schemes_list": ["BETI BACHAO BETI PADHAO", "POSHAN ABHIYAN", "PM POSHAN", "PRADHAN MANTRI POSHAN SHAKTI NIRMAN", "PRADHAN MANTRI SURAKSHIT MATRITVA ABHIYAN", "PRADHAN MANTRI MATRU VANDANA YOJANA", "SAKSHAM ANGANWADI POSHAN 2.0", "MISSION VATSALYA", "MISSION SHAKTI", "PALNA SCHEME", "SUKANYA SAMRIDDHI YOJANA", "SAMBAL", "SAMARTHYA", "NIRBHAYA FUND"],
"Organization_list": ["NCPCR", "NATIONAL COMMISSION FOR PROTECTION OF CHILD RIGHTS", "NCW", "NATIONAL COMMISSION FOR WOMEN", "UNICEF", "UNITED NATIONS CHILDREN'S FUND", "NIPCCD", "NATIONAL INSTITUTE OF PUBLIC COOPERATION AND CHILD DEVELOPMENT", "CENTRAL ADOPTION RESOURCE AUTHORITY"],
"temporary_keywords": [],
"specific_rules": []
},

"Ministry of Commerce and Industry": {
"key_officials_list": ["Piyush Goyal"],
"keywords_phrases_list": ["Ministry of Commerce and Industry", "Department for Promotion of Industry and Internal Trade", "DPIIT", "Department of Commerce", "Directorate General of Foreign Trade", "DGFT", "Union Minister Piyush Goyal", "Make in India", "Startup India", "Invest India", "Ease of Doing Business", "EoDB", "PM Gati Shakti", "National Logistics Policy", "National Industrial Corridor Development Corporation", "NIDC", "National Infrastructure Pipeline", "National Single Window System", "NSWS", "One District One Product", "ODOP", "Global Competitiveness Index", "Production Linked Incentive Scheme", "PLI", "Project Monitoring Group", "PMG", "Foreign Trade Policy", "FTP", "RoDTEP", "Remission of Duties and Taxes on Exported Products", "MEIS", "SEIS", "Special Economic Zones", "SEZs", "All Export Promotion Councils", "EPCs", "Merchandise Exports", "Services Exports", "Export performance", "WTO Negotiations", "Trade Facilitation", "India-UK FTA", "India-UAE CEPA", "India-Australia ECTA", "India-EU FTA", "Bilateral Trade Agreements", "Leather and Footwear", "Plantation", "Tea", "Coffee", "Spices", "Rubber", "Light Engineering Industry", "Electronics and Semiconductors", "Cement Industry", "Paper", "Linoleum", "Textiles and Apparel", "Marine Products", "Pharmaceuticals", "Consumer Industry", "Toy Industry", "Chemicals and Petrochemicals", "Explosives and Boilers", "Industrial Safety", "Industrial Licensing", "Foreign Direct Investment", "FDI", "Industrial Corridors", "DMIC", "BMEC", "Industrial Policy of India", "Investment Promotion", "State Startup Rankings", "BRAP", "Business Reforms Action Plan", "Global Investors Summits", "IPR Policy", "Patents", "Trademarks", "GI Tags", "Copyright", "Designs", "Geographical Indications Registry", "Cell for IPR Promotion and Management", "CIPAM", "Semiconductor Design Policy", "Controller General of Patents, Designs and Trademarks", "Public Procurement", "International Investment Treaties and Agreements", "IITA", "G20 Trade and Investment", "WTO Ministerial Conferences", "Bilateral Trade Missions", "Regional Cooperation", "ASEAN", "BIMSTEC", "RCEP", "Trade Events", "Trade Fair", "India International Trade Fair", "IITF", "Toy Fair India", "DPIIT Startup Awards", "Ease of Doing Business workshops", "Bharat Mobility", "CII", "FICCI", "ASSOCHAM", "Chambers of Commerce" ],
"Policies_schemes_list": ["Make in India", "Startup India", "PM Gati Shakti", "Production Linked Incentive Scheme", "Ease of Doing Business", "National Logistics Policy", "One District One Product", "National Single Window System", "Industrial Policy of India", "RoDTEP", "MEIS", "SEIS", "Foreign Trade Policy", "BRAP", "State Startup Rankings"],
"Organization_list": ["Ministry of Commerce and Industry", "Department for Promotion of Industry and Internal Trade", "DPIIT", "Department of Commerce", "Directorate General of Foreign Trade", "DGFT", "National Industrial Corridor Development Corporation", "National Infrastructure Pipeline", "Project Monitoring Group", "All Export Promotion Councils", "Cell for IPR Promotion and Management", "Controller General of Patents, Designs and Trademarks", "Geographical Indications Registry", "Invest India", "CIPAM", "CII", "FICCI", "ASSOCHAM"],
"temporary_keywords": [],
"specific_rules": []
},
"Ministry of Ports, Shipping and Waterways": {
"key_officials_list": ["Sarbananda Sonowal", "Shantanu Thakur", "T. K. Ramachandran"],
"keywords_phrases_list": ["Sagarmala", "Sagarmala Programme", "Sagarmala Innovation and Startup Policy", "Coastal Berth Scheme", "Harit Sagar", "Green Port Guidelines", "Maritime India Vision 2030", "Maritime Amrit Kaal Vision 2047", "Sagar Samajik Sahayog", "Shipbuilding Financial Assistance Policy", "Cruise Shipping Policy", "Major Port Land-use Policy", "Berthing Policy", "Dredging Policy", "Ports", "Major Ports", "National Waterways", "Coastal Shipping", "Inland Water Transport", "Port-led Development", "Port Modernisation", "Port Connectivity", "Dredging", "Berth Occupancy", "Container Terminal", "Ro-Ro", "Ro-Pax", "Cruise Tourism", "Shipbuilding", "Ship Repair", "Maritime Cluster", "Green Ports", "Blue Economy", "Logistics Cost", "MoPSW", "Ministry of Ports, Shipping and Waterways", "Shipping Ministry", "Ports Ministry", "Ministry of Shipping"],
"Policies_schemes_list": ["Sagarmala Programme", "Sagarmala Innovation and Startup Policy", "Coastal Berth Scheme", "Harit Sagar (Green Port Guidelines 2023)", "Maritime India Vision 2030", "Maritime Amrit Kaal Vision 2047", "Sagar Samajik Sahayog (CSR Guidelines)", "Shipbuilding Financial Assistance Policy (SBFAP)", "Cruise Shipping Policy", "Major Port Land-use Policy (2014)", "Berthing Policy for Dry Bulk Cargo (2016)", "Dredging Policy", "Stevedoring and Shore Handling Policy (2016)", "Policy for Preventing Private Sector Monopoly in Major Ports (2019)"],
"Organization_list": ["Directorate General of Shipping","DG Shipping","IWAI", "Directorate General of Lighthouses and Lightships", "Andaman & Lakshadweep Harbour Works", "Inland Waterways Authority of India", "Tariff Authority for Major Ports","TAMP", "Indian Maritime University", "Syama Prasad Mookerjee Port Authority", "Paradip Port Authority", "Visakhapatnam Port Authority", "Chennai Port Authority", "V. O. Chidambaranar Port Authority", "Cochin Port Authority", "New Mangalore Port Authority", "Mormugao Port Authority", "Dredging Corporation of India","Mumbai Port Authority", "Jawaharlal Nehru Port Authority", "Deendayal Port Authority", "Seamen’s Provident Fund Organisation", "Dock Labour Board, Kolkata", "Shipping Corporation of India", "Cochin Shipyard Limited", "Hooghly Cochin Shipyard Limited", "Central Inland Water Transport Corporation Limited", "Hooghly Dock & Port Engineers Limited", "Sagarmala Development Company Limited", "Indian Port Rail & Ropeway Corporation Limited","IPRCL", "Indian Port Global Limited", "Sethusamudram Corporation Limited", "Indian Ports Association", "Seafarers Welfare Fund Society", "Mumbai PA", "JNPA", "Deendayal PA", "Syama Prasad Mookerjee PA", "Paradip PA", "Visakhapatnam PA", "Chennai PA", "Cochin PA", "V.O.C. PA", "Mormugao PA", "New Mangalore PA"],
"temporary_keywords": [],
"specific_rules": []
},
"Ministry of Steel": {
"key_officials_list": ["H. D. Kumaraswamy", "Bhupathiraju Srinivasa Varma", "Sandeep Poundrik", "Ashish Chatterjee", "Abhijit Narendra", "Daya Nidhan Pandey", "Vinod K. Tripathi", "Sudershan Mendiratta"],
"keywords_phrases_list": ["National Steel Policy 2017", "NSP 2017", "DMI&SP Policy", "Steel Scrap Recycling Policy", "Green Steel", "Green Steel Initiative", "Green Steel Taxonomy", "Decarbonization in steel", "Raw material security", "Iron ore supply", "Coking coal import", "Specialty Steel", "PLI Scheme for Specialty Steel", "Atmanirbhar Bharat steel", "Steel sector PLI", "Iron and Steel industry", "Steel sector development", "Steel products", "Steel PSUs", "SAIL", "RINL", "NMDC", "MOIL", "KIOCL", "MSTC", "MECON", "Bird Group", "Joint Plant Committee", "NISST", "BPNSI", "ICVL", "Steel Authority of India Limited", "Rashtriya Ispat Nigam Limited", "Steel capacity 300 MT", "Crude steel production", "Per-capita steel consumption", "Value-added steel", "Auto-grade steel", "API-grade steel", "Electro-galvanized steel", "Pelletisation", "Slurry pipeline", "Steel slag road", "Slag utilisation", "Hydrogen DRI", "Green hydrogen in steel", "R&D Scheme steel", "National Metallurgist Awards", "Make in India steel", "Ministry of Steel India"],
"Policies_schemes_list": ["National Steel Policy 2017", "DMI&SP Policy 2017", "Steel Scrap Recycling Policy 2019", "Green Steel Taxonomy 2023", "Production Linked Incentive (PLI) Scheme for Specialty Steel 2021", "R&D Scheme for Iron & Steel Sector", "Green Hydrogen Pilots for Steel Sector (National Green Hydrogen Mission)", "National Metallurgist Awards Scheme", "Guidelines for Classifying Steel Producers", "Guidelines for Identification of Non-Prime Steel Products"],
"Organization_list": ["Steel Authority of India Limited", "Rashtriya Ispat Nigam Limited", "NMDC Limited", "NMDC Steel Limited", "MOIL Limited", "KIOCL Limited", "MSTC Limited", "MECON Limited", "Bird Group of Companies", "Joint Plant Committee", "National Institute of Secondary Steel Technology", "Biju Patnaik National Steel Institute","International Coal Ventures Limited"],
"temporary_keywords": [],
"specific_rules": []
},
"Ministry of Cooperation": {
"key_officials_list": ["Amit Shah", "Krishan Pal Gurjar", "Murlidhar Mohol", "Ashish Kumar Bhutani", "Pankaj Kumar Bansal", "Sanjiv Narain Mathur", "Rabindra Kumar Agarwal", "Anand Kumar Jha", "Siddharth Jain", "Dinesh Kumar Verma"],
"keywords_phrases_list": ["Cooperative Movement", "Sahakar se Samriddhi", "Multi-State Cooperative Societies", "MSCS Act 2002", "Central Registrar of Cooperative Societies", "CRCS", "Primary Agricultural Credit Societies", "PACS", "Computerization of PACS", "Model PACS Byelaws", "PACS as Common Service Centres", "PACS LPG dealership", "PACS Petrol Pump dealership", "Jan Aushadhi Kendra by PACS", "Formation of FPOs in Cooperative Sector", "World’s Largest Grain Storage Plan", "2 Lakh New Multipurpose PACS", "National Cooperative Database", "Ease of Doing Business for Cooperatives", "Register New Multi-State Cooperative Society", "Cooperative Trainings", "Loans and Assistance to Cooperatives", "NCDC financing", "Yuva Sahakar", "Sahakar Pragya", "Sahakar Mitra Internship", "National Cooperative Policy", "Computerization of RCS Offices", "Cooperative Sugar Mills Scheme", "Cooperative Credit Structure", "Urban Cooperative Banks", "State Cooperative Banks", "District Central Cooperative Banks", "Dairy Cooperatives", "Fishery Cooperatives", "Multipurpose Cooperative Societies", "Seed Cooperative", "BBSSL", "Organic Cooperative", "NCOL", "National Cooperative Exports", "NCEL", "IFFCO", "KRIBHCO", "NAFED", "Nafscob", "Cooperative Fertilizer", "Cooperative Marketing Federation", "Cooperative Housing", "Cooperative Education", "Cooperative Training Institutes", "Cooperative Tax Deduction 80P", "Cooperative Governance", "One Nation One Cooperative Society", "Digital Cooperative Services", "e-governance for Cooperatives"],
"Policies_schemes_list": ["National Cooperative Policy (draft)", "Computerization of PACS Scheme", "Computerization of RCS Offices Project", "Production and Marketing of Organic Produce through National Cooperative Organics Limited", "PACS Model Byelaws Initiative", "PACS as Common Service Centre Scheme", "Jan Aushadhi Kendras by PACS Scheme", "FPO Formation in Cooperative Sector Scheme", "World’s Largest Grain Storage Plan in Cooperative Sector", "2 Lakh New PACS/Dairy/Fishery Cooperatives Initiative", "Grant-in-Aid Scheme for Cooperative Sugar Mills (NCDC)", "Yuva Sahakar – Start-up Scheme", "Sahakar Pragya Capacity Building Programme", "Sahakar Mitra Internship Program"],
"Organization_list": ["National Cooperative Development Corporation", "National Council for Cooperative Training", "Central Registrar of Cooperative Societies", "Vaikunth Mehta National Institute of Cooperative Management", "Bharatiya Beej Sahakari Samiti Limited", "National Cooperative Exports Limited", "National Cooperative Organics Limited"],
"temporary_keywords": [],
"specific_rules": []
},
"Ministry of Agriculture and Farmers Welfare": {
"key_officials_list": ["Shivraj Singh Chouhan", "Ram Nath Thakur", "Bhagirath Choudhary", "Devesh Chaturvedi", "Sanjiv Narain Mathur", "Pramod Kumar Meherda", "Praveen Kumar Singh"],
"keywords_phrases_list": ["Agriculture & Farmers Welfare", "Department of Agriculture & Farmers Welfare", "DA&FW", "Department of Agricultural Research & Education", "DARE", "Indian Council of Agricultural Research", "ICAR", "Agricultural Marketing", "Crop Insurance", "Agricultural Credit", "Kisan Credit Card", "KCC", "Soil Health Management", "Organic Farming", "Plant Protection", "Farm Mechanization", "Krishi Vigyan Kendra", "KVK", "Digital Extension", "mKisan", "Kisan Call Center", "AGMARKNET", "Doubling Farmers Income", "Climate-Resilient Agriculture", "Sustainable Farming", "Farm-Gate Infrastructure"],
"Policies_schemes_list": ["Agriculture Infrastructure Fund (AIF)", "Pradhan Mantri Kisan Samman Nidhi (PM-KISAN)", "Pradhan Mantri Fasal Bima Yojana (PMFBY)", "Pradhan Mantri Krishi Sinchayee Yojana (PMKSY)", "Rashtriya Krishi Vikas Yojana (RKVY-RAFTAAR)", "National Food Security Mission (NFSM)", "National Mission for Sustainable Agriculture (NMSA)", "Mission for Integrated Development of Horticulture (MIDH)", "Soil Health Card Scheme", "National Agriculture Market (e-NAM)", "Paramparagat Krishi Vikas Yojana (PKVY)", "Sub-Mission on Agricultural Extension (ATMA)", "Digital Agriculture Mission", "Direct Benefit Transfer in Agriculture (DBT-A)", "Pradhan Mantri Kisan Maandhan Yojana (PM-KMY)"],
"Organization_list": ["Indian Council of Agricultural Research (ICAR)", "National Institute of Agricultural Extension Management (MANAGE)", "Chaudhary Charan Singh National Institute of Agricultural Marketing (NIAM)", "National Institute of Plant Health Management (NIPHM)", "National Rainfed Area Authority (NRAA)", "Central Institute of Horticulture (CIH)", "National Centre for Cold-Chain Development (NCCD)", "Coconut Development Board (CDB)", "National Bee Board (NBB)", "National Horticulture Board (NHB)", "National Seeds Corporation (NSC)", "Commission for Agricultural Costs and Prices (CACP)", "Directorate of Marketing and Inspection (DMI)", "Directorate of Plant Protection, Quarantine & Storage (PPQS)", "Mahalanobis National Crop Forecast Centre (NCFC)", "Soil and Land Use Survey of India (SLUSI)", "National Seed Research and Training Centre (NSRTC)", "Central Fertilizer Quality Control & Training Institute (CFQCTI)"],
"temporary_keywords": [],
"specific_rules": []
}
}

    ## END OF META DATA FOR MINISTRIES ##

    7. **Author Identification:**
    -If the name of author is mentioned in the news article, extract it and store it in author_name field.


      **Return ONLY valid JSON with this exact structure and nothing else:**
      {
        "language": "...",
        "heading": "...",
        "content": "...",
        "english_heading": "...",
        "english_content": "...",
        "english_summary": "...",
        "sentiment": "positive" | "negative" | "neutral",
        "ministries": [ { "ministry": "..."} ],
        "date": "dd-mm-yyyy" | "unknown",
        "author_name": "..."
      }
      Ensure "ministries" is always an array, even if empty. Ensure "date" is in dd-mm-yyyy format or exactly "unknown".
      If "ministries" have empty array, set values of "language", "heading", "content", "english_heading", "english_content", "english_summary", "sentiment", "author_name" as "unknown"
      DO NOT wrap the JSON in Markdown or code fences.

"""


AD_CHECK_MODEL_NAME = os.getenv("GEMINI_AD_MODEL", "gemini-1.5-pro")
AD_CHECK_GENERATION_CONFIG = types.GenerationConfig(candidate_count=1, max_output_tokens=256) # 256 should be plenty for the ad check JSON
AD_CHECK_PROMPT = """             
        Look at this newspaper image block and decide if it should be treated as "ministry content" or "advertisement."
        — If the block is about a government ministry (news, announcements, events, statements), it's ministry content.
        — Anything else—ads, promos, coupons, pricing info, logos, unrelated images, masthead elements, or generic graphics—is an advertisement.
        Return ONLY a JSON object with:
        {"is_advertisement": true|false, "confidence": "high"|"medium"|"low", "reasoning": "brief explanation"}
        Guidance:
        • Ministry content: official announcements, policy updates, bylines or headlines referencing a ministry, dates or events issued by a ministry.
        • Advertisement: sales pitches, business contact info, coupon codes, pricing, branding, decorative graphics, masthead logos, or any non-ministry text/image.
        • Ignore small or decorative images and mastheads—they count as advertisements.
        • If unsure, lean towards "advertisement" to avoid misclassifying ministry content.
        • If the image is too blurry or unclear to analyze, return {"is_advertisement": true, "confidence": "low", "reasoning": "image too unclear to analyze"}
        • consider tender notices and job postings and other govenment notices as advetisement. 
        ====== 
        • consider tender notices and job postings and other govenment notices as advetisement.        • Respond with ONLY valid JSON.
        • Do not include any commentary or conversational text.
        • If the image contains more than one block, focus on the dominant block.
        • If the image is a composite, focus on the main content.
        • If the image contains both ministry content and advertisement, focus on the dominant content.
        • If the image contains a QR code, consider it as an advertisement.
        • If the image contains a tender notice or a job posting, consider it as an advertisement.
        • If the image contains a government notice, consider it as an advertisement.
        • If the image contains a press release, consider it as ministry content.
        • If the image contains a government scheme, consider it as ministry content.
        • If the image contains a government event, consider it as ministry content.
        • If the image contains a government statement, consider it as ministry content.
        • If the image contains a government announcement, consider it as ministry content.
        • If the image contains a government policy, consider it as ministry content.
        • If the image contains a government initiative, consider it as ministry content.
        • If the image contains a government program, consider it as ministry content.
        • If the image contains a government campaign, consider it as ministry content.
        • If the image contains a government achievement, consider it as ministry content.
        • If the image contains a government award, consider it as ministry content.
        • If the image contains a government recognition, consider it 
        """
TEXT_AD_CHECK_INSTRUCTION = """
        Analyze at this textual block from a digital news site and decide if it is an "advertisement" or "indian ministry news content. or realted to indian ministry content"\
         for classification analyse the content properly if the content is related to ministry or not.
         **Ministry Analysis:** 

            - Ministry of Agriculture and Farmers' Welfare
            - Ministry of Animal Husbandry Dairying and Fisheries
            - Ministry of AYUSH
            - Ministry of Chemicals and Fertilizers
            - Ministry of Civil Aviation
            - Ministry of Coal
            - Ministry of Commerce and Industry
            - Ministry of Communications
            - Ministry of Consumer Affairs Food and Public Distribution System
            - Ministry of Cooperation
            - Ministry of Corporate Affairs
            - Ministry of Culture
            - Ministry of Defence
            - Ministry of Development of North Eastern Region
            - Ministry of Earth Sciences
            - Ministry of Education
            - Ministry of Electronics and Information Technology
            - Ministry of Environment Forest and Climate Change
            - Ministry of Finance
            - Ministry of Food Processing Industries
            - Ministry of Health and Family Welfare
            - Ministry of Heavy Industries
            - Ministry of Home Affairs
            - Ministry of Housing and Urban Affairs
            - Ministry of Information and Broadcasting
            - Ministry of Jal Shakti
            - Ministry of Labour and Employment
            - Ministry of Law and Justice
            - Ministry of Micro Small and Medium Enterprises
            - Ministry of Mines
            - Ministry of Minority Affairs
            - Ministry of New and Renewable Energy
            - Ministry of Panchayati Raj
            - Ministry of Parliamentary Affairs
            - Ministry of Personnel Public Grievances and Pensions
            - Ministry of Petroleum and Natural Gas
            - Ministry of Ports Shipping and Waterways
            - Ministry of Power
            - Ministry of Railways
            - Ministry of Road Transport and Highways
            - Ministry of Rural Development
            - Ministry of Science and Technology
            - Ministry of Skill Development and Entrepreneurship
            - Ministry of Social Justice and Empowerment
            - Ministry of Statistics and Programme Implementation
            - Ministry of Steel
            - Ministry of Textiles
            - Ministry of Tourism
            - Ministry of Tribal Affairs
            - Ministry of Women and Child Development
            - Ministry of Youth Affairs and Sports
            - Ministry of External Affairs
            - Prime Minister's Office
            - NITI Aayog
        - the conent should be related to above listed ministry content only
        — Ministry news content: official announcements, policy updates, statements by ministers, etc.
        — Advertisement: sales copy, brand promotions, coupon codes, unrelated marketing text.
        Return ONLY valid JSON, for example:
        {"is_advertisement": true|false, "confidence": "high"|"medium"|"low", "reasoning": "brief explanation"}
        """
TEXT_AD_CHECK_MODEL_NAME = os.getenv("GEMINI_AD_MODEL", "gemini-1.5-pro")
TEXT_AD_CHECK_GENERATION_CONFIG = types.GenerationConfig(candidate_count=1, max_output_tokens=256) # 256 should be plenty for the ad check JSON


DIGITAL_TEXT_ANALYSIS_MODEL_NAME = os.getenv("GEMINI_TEXT_ANALYSIS_MODEL", "gemini-2.0-flash") # Can be same or different
DIGITAL_TEXT_ANALYSIS_GENERATION_CONFIG = types.GenerationConfig(
    candidate_count=1, stop_sequences=[], max_output_tokens=2048 # May need less for text
)
DIGITAL_TEXT_ANALYSIS_SYSTEM_INSTRUCTION = """You are an expert content analyst. Given the following article text (and optionally an original heading and language):
1.  **Language Confirmation/Detection:** If a language is provided, confirm it. If not, detect it.
2.  **Translation:** If the original language of the content is not English, translate the heading (if provided) and the main content into English.
3.  **English Summary:** Provide a concise 2-3 sentence summary of the English content.
4.  **Sentiment Analysis:**  Determine the overall sentiment of the translated (or original English) article,
 
            Classification Rules:
            - If the content is political (about the Indian government, its leaders, or policies), predict the sentiment based on whether it highlights actions or decisions that positively benefit the Indian government, its stability, or its performance.
            - If the content is non-political (general news or content), classify the sentiment based on whether it highlights actions or developments that benefit India's national well-being, safety, prosperity, or international image.
            - If the content mentions actions taken by the Indian government or security forces to protect the country, address security threats (e.g., terrorism, extremism), or ensure national peace, classify it as Positive.
            - If the content involves achievements, advancements, or milestones for India, or positive contributions to international relations or India's image, classify it as Positive.
            - If the content mentions harm or negative effects on India's environment, economy, or stability, classify it as Negative.
            - If the content involves accusations or issues that could damage trust in India's democracy, institutions, or reputation, classify it as Negative.
            - If a negative event (e.g., crime, disaster, violence) occurs but the Indian government or institutions take strong, effective action to resolve the issue, classify the sentiment as Neutral.
            - If the content portrays India as a leader in positive areas, such as innovation, national defense, global cooperation, or social progress, classify it as Positive.
            
            Special Considerations:
            - If the content involves India taking proactive, strong steps to counter terrorism, extremism, or unrest, classify the sentiment as Positive.
            - If the content showcases India or its government making strides in improving national security, prosperity, or overall public welfare, classify the sentiment as Positive.
            - If the content focuses on a negative event but the response by the government or citizens brings about a positive outcome or demonstrates resilience, classify the sentiment as Neutral.
            
            Detailed Sentiment Logic:
            - Positive: Content that showcases India, the Indian government, or any aspect of India's society or systems in a favorable light, demonstrating strength, progress, or success.
            - Negative: Content that focuses on damage, harm, instability, or anything that negatively impacts India or its image.
            - Neutral: Content that does not strongly impact India's well-being in a negative or positive way, or where negative aspects are countered by effective actions.
            
            Respond with ONLY ONE WORD: 'positive', 'negative', or 'neutral'.
5.  **Ministry Analysis:** Based on the main topics, identify the top 3 Indian government ministries most relevant or responsible for addressing the issues mentioned. Choose ONLY from the following comprehensive list. If fewer than 3 are clearly relevant, still provide the list structure with fewer items. If none are clearly relevant, return an empty list `[]`.

            - Ministry of Agriculture and Farmers' Welfare
            - Ministry of Animal Husbandry Dairying and Fisheries
            - Ministry of AYUSH
            - Ministry of Chemicals and Fertilizers
            - Ministry of Civil Aviation
            - Ministry of Coal
            - Ministry of Commerce and Industry
            - Ministry of Communications
            - Ministry of Consumer Affairs Food and Public Distribution System
            - Ministry of Cooperation
            - Ministry of Corporate Affairs
            - Ministry of Culture
            - Ministry of Defence
            - Ministry of Development of North Eastern Region
            - Ministry of Earth Sciences
            - Ministry of Education
            - Ministry of Electronics and Information Technology
            - Ministry of Environment Forest and Climate Change
            - Ministry of Finance
            - Ministry of Food Processing Industries
            - Ministry of Health and Family Welfare
            - Ministry of Heavy Industries
            - Ministry of Home Affairs
            - Ministry of Housing and Urban Affairs
            - Ministry of Information and Broadcasting
            - Ministry of Jal Shakti
            - Ministry of Labour and Employment
            - Ministry of Law and Justice
            - Ministry of Micro Small and Medium Enterprises
            - Ministry of Mines
            - Ministry of Minority Affairs
            - Ministry of New and Renewable Energy
            - Ministry of Panchayati Raj
            - Ministry of Parliamentary Affairs
            - Ministry of Personnel Public Grievances and Pensions
            - Ministry of Petroleum and Natural Gas
            - Ministry of Ports Shipping and Waterways
            - Ministry of Power
            - Ministry of Railways
            - Ministry of Road Transport and Highways
            - Ministry of Rural Development
            - Ministry of Science and Technology
            - Ministry of Skill Development and Entrepreneurship
            - Ministry of Social Justice and Empowerment
            - Ministry of Statistics and Programme Implementation
            - Ministry of Steel
            - Ministry of Textiles
            - Ministry of Tourism
            - Ministry of Tribal Affairs
            - Ministry of Women and Child Development
            - Ministry of Youth Affairs and Sports
            - Ministry of External Affairs
            - Prime Minister's Office
            - NITI Aayog
            
            
            IMPORTANT PRIORITY: If any key ministers are mentioned in the article, their ministry should be listed for sure. Use these COMPLETE mappings (note that some ministers handle multiple ministries, but only pick the one most relevant to the content):
       
        - PM Modi, PM, PMO, pmo, Narendar Modi, Modi, Prime Minister Modi, Narendra Modi → Prime Minister's Office, Ministry of Personnel Public Grievances and Pensions, NITI Aayog
        - Shivraj Singh Chouhan → Ministry of Agriculture and Farmers' Welfare, Ministry of Rural Development
        - Lalan Singh → Ministry of Animal Husbandry Dairying and Fisheries, Ministry of Panchayati Raj
        - Prataprao Jadhav → Ministry of AYUSH
        - J. P. Nadda → Ministry of Chemicals and Fertilizers, Ministry of Health and Family Welfare
        - Kinjarapu Ram Mohan Naidu → Ministry of Civil Aviation
        - G. Kishan Reddy → Ministry of Coal, Ministry of Mines
        - Piyush Goyal → Ministry of Commerce and Industry
        - Jyotiraditya Scindia → Ministry of Communications, Ministry of Development of North Eastern Region
        - Pralhad Joshi → Ministry of Consumer Affairs Food and Public Distribution, Ministry of New and Renewable Energy
        - Amit Shah → Ministry of Cooperation, Ministry of Home Affairs
        - Nirmala Sitharaman → Ministry of Corporate Affairs, Ministry of Finance
        - Gajendra Singh Shekhawat → Ministry of Culture, Ministry of Tourism
        - Rajnath Singh → Ministry of Defence
        - Dr. Jitendra Singh → Ministry of Earth Sciences, Ministry of Science and Technology
        - Dharmendra Pradhan → Ministry of Education
        - Ashwini Vaishnaw → Ministry of Electronics and Information Technology, Ministry of Information and Broadcasting, Ministry of Railways
        - Bhupender Yadav → Ministry of Environment Forest and Climate Change
        - S. Jaishankar → Ministry of External Affairs
        - Chirag Paswan → Ministry of Food Processing Industries
        - H. D. Kumaraswamy → Ministry of Heavy Industries, Ministry of Steel
        - Manohar Lal Khattar → Ministry of Housing and Urban Affairs
        - Manohar Lal → Ministry of Power
        - C. R. Patil → Ministry of Jal Shakti
        - Mansukh Mandaviya → Ministry of Labour and Employment, Ministry of Youth Affairs and Sports
        - Arjun Ram Meghwal → Ministry of Law and Justice
        - Jitan Ram Manjhi → Ministry of Micro Small and Medium Enterprises
        - Kiren Rijiju → Ministry of Minority Affairs, Ministry of Parliamentary Affairs
        - Hardeep Singh Puri → Ministry of Petroleum and Natural Gas
        - Rao Inderjit Singh → Ministry of Planning, Ministry of Statistics and Programme Implementation
        - Sarbananda Sonowal → Ministry of Ports Shipping and Waterways
        - Nitin Gadkari → Ministry of Road Transport and Highways
        - Jayant Chaudhary → Ministry of Skill Development and Entrepreneurship
        - Virendra Kumar Khatik → Ministry of Social Justice and Empowerment
        - Giriraj Singh → Ministry of Textiles
        - Jual Oram → Ministry of Tribal Affairs
        - Annpurna Devi → Ministry of Women and Child Development
        
        SPECIAL RULES FOR PRIME MINISTER'S OFFICE:
        The Prime Minister's Office should ONLY be classified when:
        1. The content directly discusses PM Modi's personal actions, decisions, or statements
        2. The content relates to major national policy announcements or initiatives led by the PM
        3. International diplomacy or foreign visits involving the PM
        4. National security matters requiring PM's direct involvement
        5. Major economic reforms or flagship programs announced by the PM
        6. Critical national emergencies or crises requiring PM's intervention

        DO NOT classify as PMO for:
        - Local news or regional events
        - Temple inaugurations or religious/cultural events unless PM is personally presiding
        - State-level political developments
        - Minor political party news
        - Regular government schemes not specifically led by PM
        
        When a minister name is mentioned, only assign their ministry that is most relevant to the content being discussed and give that ministry more priority in the confidence score.

            
    6.  **Date Extraction:** If a publication date is explicitly mentioned *within the provided text content*, extract it in dd-mm-yyyy format. Otherwise, respond with "" for the date. Do not infer from context outside the provided text.

    You MUST respond with valid JSON. The JSON must have the following structure:
    {
        "language": "Detected or confirmed language of input text",
        "original_heading_provided": "...", /* The original heading if it was input */
        "original_content_provided_snippet": "...", /* Snippet of original content for verification */
        "english_heading": "...", /* Translated heading, or original if English */
        "english_content": "...", /* Translated content, or original if English */
        "english_summary": "...",
        "sentiment": "POSITIVE" | "NEGATIVE" | "NEUTRAL",
        "ministries": [ { "ministry": "..." } ], /* up to 3  */
        "date_from_text": "dd-mm-yyyy" | "" /* Date EXPLICITLY found in text */
    }"""
# Global (per-process) model instances, initialized by init_models_for_process()
content_analyzer_model_instance = None
ad_checker_model_instance = None
digital_text_analyzer_model_instance = None # NEW
text_ad_checker_model_instance = None
def init_models_for_process():
    global content_analyzer_model_instance, ad_checker_model_instance, text_ad_checker_model_instance, digital_text_analyzer_model_instance # Allow modification
    pid = os.getpid()
    if PROCESS_SPECIFIC_GEMINI_KEY: # Check if SDK was successfully configured
        try:
            print(f"Process {pid}: Initializing Gemini models (Content: {CONTENT_ANALYSIS_MODEL_NAME}, Ad: {AD_CHECK_MODEL_NAME}).")
            content_analyzer_model_instance = genai.GenerativeModel(
                model_name=CONTENT_ANALYSIS_MODEL_NAME,
                generation_config=CONTENT_ANALYSIS_GENERATION_CONFIG,
                system_instruction=CONTENT_ANALYSIS_SYSTEM_INSTRUCTION
            )
            ad_checker_model_instance = genai.GenerativeModel(
                model_name=AD_CHECK_MODEL_NAME,
                generation_config=AD_CHECK_GENERATION_CONFIG
                # AD_CHECK_PROMPT is passed as content to generate_content, not as system_instruction here
            )

            #textual Ad check model init
            text_ad_checker_model_instance = genai.GenerativeModel(
                model_name=TEXT_AD_CHECK_MODEL_NAME,
                generation_config=TEXT_AD_CHECK_GENERATION_CONFIG,
                system_instruction=TEXT_AD_CHECK_INSTRUCTION
                # AD_CHECK_PROMPT is passed as content to generate_content, not as system_instruction here
            )
            digital_text_analyzer_model_instance = genai.GenerativeModel( # Initialize new model
                model_name=DIGITAL_TEXT_ANALYSIS_MODEL_NAME,
                generation_config=DIGITAL_TEXT_ANALYSIS_GENERATION_CONFIG,
                system_instruction=DIGITAL_TEXT_ANALYSIS_SYSTEM_INSTRUCTION
            )
            if content_analyzer_model_instance and ad_checker_model_instance and text_ad_checker_model_instance and digital_text_analyzer_model_instance:
                print(f"Process {pid}: ALL Gemini models initialized successfully in config module.")
            else:
                missing_models = []
                if not content_analyzer_model_instance: missing_models.append("ContentAnalysis")
                if not ad_checker_model_instance: missing_models.append("AdCheck")
                if not text_ad_checker_model_instance: missing_models.append("text_AdCheck")
                if not digital_text_analyzer_model_instance: missing_models.append("DigitalTextAnalysis")
                print(f"Process {pid}: Some Gemini models FAILED to initialize: {', '.join(missing_models)}")
        except Exception as e:
            print(f"Process {pid}: CRITICAL Error during init_models_for_process in config.py: {e}")
            import traceback; traceback.print_exc()
            content_analyzer_model_instance = None
            ad_checker_model_instance = None
            text_ad_checker_model_instance = None
            digital_text_analyzer_model_instance = None
    else:
        print(f"Process {pid}: Skipping model initialization as PROCESS_SPECIFIC_GEMINI_KEY is not set.")

# --- Getter functions for models ---
def get_configured_ad_checker_model():
    if not ad_checker_model_instance: print(f"Process {os.getpid()}: Ad checker model accessed but is None.")
    return ad_checker_model_instance

def get_configured_text_ad_checker_model():
    if not text_ad_checker_model_instance: print(f"Process {os.getpid()}: Ad checker model accessed but is None.")
    return text_ad_checker_model_instance
def get_configured_content_analyzer_model(): # This is for IMAGE based newspaper articles
    if not content_analyzer_model_instance: print(f"Process {os.getpid()}: Image content analyzer model accessed but is None.")
    return content_analyzer_model_instance

def get_configured_digital_text_analyzer_model(): # NEW getter
    if not digital_text_analyzer_model_instance: print(f"Process {os.getpid()}: Digital text analyzer model accessed but is None.")
    return digital_text_analyzer_model_instance

# --- AWS S3 Client ---
AWS_S3_BUCKET_NAME_CONFIG = os.getenv('AWS_S3_BUCKET_NAME')
AWS_REGION_CONFIG = os.getenv('AWS_S3_REGION', 'ap-south-1')
AWS_ACCESS_KEY_ID_CONFIG = os.getenv('AWS_ACCESS_KEY_ID')
AWS_SECRET_ACCESS_KEY_CONFIG = os.getenv('AWS_SECRET_ACCESS_KEY')

s3_client = None # This s3_client will be initialized once per module load (effectively per process)
if AWS_S3_BUCKET_NAME_CONFIG and AWS_ACCESS_KEY_ID_CONFIG and AWS_SECRET_ACCESS_KEY_CONFIG:
    try:
        s3_client = boto3.client(
            's3',
            region_name=AWS_REGION_CONFIG,
            aws_access_key_id=AWS_ACCESS_KEY_ID_CONFIG,
            aws_secret_access_key=AWS_SECRET_ACCESS_KEY_CONFIG
        )
        print(f"OCR Engine Config: S3 client configured for bucket '{AWS_S3_BUCKET_NAME_CONFIG}'.")
    except Exception as e_s3:
        print(f"⚠️ WARNING (OCR Engine Config): Failed to initialize S3 client. Error: {e_s3}")
else:
    print("⚠️ WARNING (OCR Engine Config): S3 credentials for worker not fully set. S3 operations will fail.")

# --- PIL Config ---
from PIL import Image as PIL_Image
PIL_Image.MAX_IMAGE_PIXELS = None