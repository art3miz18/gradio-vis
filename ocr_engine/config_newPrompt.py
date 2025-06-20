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
CONTENT_ANALYSIS_MODEL_NAME = os.getenv("GEMINI_CONTENT_MODEL", "gemini-2.0-flash")
CONTENT_ANALYSIS_GENERATION_CONFIG = types.GenerationConfig(
    candidate_count=1, stop_sequences=[], max_output_tokens=4096
)
CONTENT_ANALYSIS_SYSTEM_INSTRUCTION = """ You are a highly skilled newspaper content analyst. You are provided with the full text of a newspaper article. Perform the following tasks:

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
                                                      
    6. **Ministry Analysis:** Based on the main topics, identify up to THREE Indian government ministries that are most relevant to the issues mentioned, chosen only from the list provided below. If fewer than three are relevant, return fewer; if none, return an empty list [].
    Evaluate the full article carefully and identify up to **three Indian government ministries** that are most contextually relevant to the **central topics, implications, or governmental scope of action**. Your classification should reflect a deep understanding of:
    - Which ministries are **likely responsible or impacted**
    - Which policies, schemes, administrative roles, or governance functions are **core to the discussion**
    - **Who is being mentioned in what capacity**, and whether the **intent or outcome** aligns with a specific ministry’s domain.
                                                  
    Ministry List (choose from these exact names):
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

    IMPORTANT PRIORITY: If any key ministers are mentioned, ensure their ministry is listed. Use the mappings exactly as provided in the original prompt (e.g., “PM Modi” → Prime Minister's Office, etc.).
       
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
                                          
    Use the **below special classification rules and reference lists** *only when the article is ambiguous, lacking clear policy/domain context, or when your confidence is low*. In such cases, treat the rules as additional decision support — **not as hard-coded filters**.
    This analysis is meant to simulate how a human expert would classify the article: based on **intent, relevance, responsibility, and administrative fit**, rather than just string-matching. 

    ▶ Special Classification Rules for Ministry of Electronics and Information Technology (MeitY):
      Classify an article under this ministry **if it includes any of the following contextual relevance/references** from the points below:                                               
      - If any of the following key officials are mentioned in the article from the list `key_officials_list`, treat them as a strong signal that the associated ministry:  
        `key_officials_list = [Ashwini Vaishnaw, Jitin Prasada, S. Krishnan, Abhishek Singh, Amitesh Kumar Sinha, Rajesh Singh, Sushil Pal, Krishan Kumar Singh]`
      - If any of the following keywords/phrases appear (case-insensitive) in the article from the list `keywords_phrases_list`, treat them as a strong signal that the associated ministry:  
        `keywords_phrases_list = [Digital India, India Stack, CoWIN, MyGov, DigiLocker, Bhashini, AI in governance, National AI Mission, cyber policy, DPI, API Setu, App Store India, UMANG, ONDC, Common Services Centres, Digital Village program, chip design, fabrication, ATMP, Chips to Startup, Foxconn, Applied Materials, Lam Research, e-KYC, Aadhaar Face Authentication, Aadhaar authentication, AI for Good Governance, National e-Governance Division, eOffice, eCabinet, Foundations and Risk Mitigation in AI/ML, AI Adoption for Enhanced Governance, AI Tools for Smarter Public Administration, Building Robust AI Infrastructure, AI-related risks, OpenForge, National Cloud Services, GI Cloud, MeghRaj, DIKSHA platform, Government e-Marketplace, eSanjeevani, e-Hospital, Techade, National Supercomputing Mission, India Innovation Centre for Graphene, Global Value Chains, Electronics Manufacturing Clusters, Electronics Systems Design and Manufacturing, ESDM sector, IECT, ICT sector, IT Hardware manufacturing sector, M-SIPS, Viability Gap Funding, BPO, ITeS, STPI, EHTP, Electronic Hardware Technology Park, Ready Built Factory, Plug and Play facilities, Government-to-Citizen e-Services, TIDE, Technology Incubation and Development of Entrepreneurs]`
      - If any of the following Policies/Schemes appear (case-insensitive) in the article from the list `Policies_schemes_list`, treat them as a strong signal that the associated ministry:  
        `Policies_schemes_list = [Chips to Startup (C2S), Common Services Centres, Digital Village program, Technology Incubation and Development of Entrepreneurs (TIDE), AI for Good Governance, Digital Infrastructure for Knowledge Sharing (DIKSHA), MeghRaj, National Supercomputing Mission, Electronics Manufacturing Clusters, Electronics System Design and Manufacturing (ESDM), Modified Special Incentive Package Scheme (M-SIPS), Viability Gap Funding (VGF) for BPO/ITeS]`
      - If any of the following Organizations appear (case-insensitive) in the article from the list `Organization_list`, treat them as a strong signal that the associated ministry:  
        `Organization_list = [National e-Governance Division, Software Technology Parks of India, Electronic Hardware Technology Park, Government e-Marketplace, India Innovation Centre for Graphene, OpenForge, National Cloud Services, GI Cloud, MeghRaj]`
      Use these signals to **classify the article under**:`"Ministry of Electronics and Information Technology"`

    ▶ Special Classification Rules for Prime Minister's Office (PMO):
      Classify an article under this ministry **if it includes any of the following contextual relevance/references** from the points below:                                               
      - If any of the following key officials are mentioned in the article from the list `key_officials_list`, treat them as a strong signal that the associated ministry:  
        `key_officials_list = [PM Modi, Prime Minister Modi, Narendra Modi, Narendar Modi, Modi, PM, PMO, pmo, Dr. P. K. Mishra, Ajit Doval, Shaktikanta Das, Amit Khare, Tarun Kapoor, Vivek Kumar, Hardik Satishchandra Shah, Nidhi Tewari]`
      - If any of the following keywords/phrases appear (case-insensitive) in the article from the list `keywords_phrases_list`, treat them as a strong signal that the associated ministry:  
        `keywords_phrases_list = [Prime Minister's Visit, Bilateral Summit, Modi, Pradhan Mantri, PM's Intervention, PM's Statement, PM's Message, PM's Participation, PM's Virtual Address, PM's Bilateral Meetings, PM's Interaction with Diaspora, PMO Coordination, PMO Oversight, PMO-led Initiative, Mann ki Baat, PMO Monitoring, PMO Review, PMO Approval, PMO Guidance, PMO Briefing, Modi 3.0, PMO India]`
      - If any of the following Policies/Schemes appear (case-insensitive) in the article from the list `Policies_schemes_list`, treat them as a strong signal that the associated ministry:  
        `Policies_schemes_list = [Digital India, Make in India, Swachh Bharat, Atmanirbhar Bharat, Vasudhaiva Kutumbakam, International Day of Yoga, Voice of Global South, PM Vishwakarma Yojana, PM eBus Seva, PM Poshan Shakti Nirman Abhiyaan, PM SVANidhi, PM Garib Kalyan Rojgar Abhiyaan, PM Matsya Sampada Yojana, PM Kisan Samman Nidhi, PM Kisan Urja Suraksha Evam Utthan Mahabhiyan, PM Shram Yogi Mandhan, PM Annadata Aay Sanrakshan Abhiyan, PM Jan Vikas Karyakaram, PM Matritva Vandana Yojana, PM Ujjwala Yojana, PM Fasal Bima Yojana, PM Krishi Sinchai Yojana, PM Mudra Yojana, PM Gramin Awas Yojana, PM Awaas Yojana - (Urban), PM Suraksha Bima Yojana, PM Kaushal Vikas Yojna, PM Bhartiya Jan Aushadhi Kendra, PM Jan Dhan Yojana, PM Adarsh Gram Yojana]`
      Use these signals to **classify the article under**: `"Prime Minister's Office"`

    ▶ Special Classification Rules for Ministry of Defence:
      Classify an article under this ministry **if it includes any of the following contextual relevance/references** from the points below:                                               
      - If any of the following key officials are mentioned in the article from the list `key_officials_list`, treat them as a strong signal that the associated ministry:  
        `key_officials_list = [Rajnath Singh, Sanjay Seth, Rajesh Kumar Singh]`
      - If any of the following keywords/phrases appear (case-insensitive) in the article from the list `keywords_phrases_list`, treat them as a strong signal that the associated ministry:  
        `keywords_phrases_list = [Indian Army, Indian Air Force, Indian Navy, integrated defence staff, Chief of Defence Staff, Northern Command, Western Command, Southern Command, Eastern Command, Central Command, South Western Command, Army Training Command, Border Roads Organization, Directorate General Defence Estates, National Defence College, National Cadets Corps, Institute for Defence Studies and Analysis, School of Foreign Language, Armed Forces Tribunal, Armed Forces Medical College, Military Engineering Services, College of Defence Management, Defence Services Staff College, Indian Coast Guard, Services Sports Control Board, Controller General of Defence Accounts, NCC Cadets, National Defence Academy, Commanding-in-Chief, Ati Vishisht Seva Medal, Param Vishisht Seva Medal, Uttam Yudh Seva Medal, Sena Medal, National War Memorial, Military Nursing Service, Operation Sindoor]`
      - If any of the following Policies/Schemes appear (case-insensitive) in the article from the list `Policies_schemes_list`, treat them as a strong signal that the associated ministry:  
        `Policies_schemes_list = [Agnipath Scheme, Prime Minister's Scholarship Scheme (PMSS), Defence Testing Infrastructure Scheme (DTIS), Ex-Servicemen Welfare Schemes, Army Surplus Vehicles to ESM/Widows, National Defence Fund Scholarship, Welfare Schemes of Kendriya Sainik Board (KSB), iDEX - Innovations for Defence Excellence, Technology Development Fund (TDF), SRIJAN Portal]`
      - If any of the following Organizations appear (case-insensitive) in the article from the list `Organization_list`, treat them as a strong signal that the associated ministry:  
        `Organization_list = [Department of Defence (DoD), Department of Military Affairs (DMA), Department of Defence Production (DDP), Department of Defence Research and Development (DRDO), Department of Ex-Servicemen Welfare (DESW), Hindustan Aeronautics Limited (HAL), Bharat Electronics Limited (BEL), Bharat Dynamics Limited (BDL), BEML Limited (BEML), Mazagon Dock Shipbuilders Limited (MDL), Garden Reach Shipbuilders and Engineers Limited (GRSE), Mishra Dhatu Nigam Limited (MIDHANI), Armoured Vehicles Nigam Limited (AVNL), Advanced Weapons and Equipment India Limited (AWEIL), Munitions India Limited (MIL), Yantra India Limited (YIL), India Optel Limited (IOL), Troop Comforts Limited (TCL), Gliders India Limited (GIL)]`
      Use these signals to **classify the article under**: `"Ministry of Defence"`

    ▶ Special Classification Rules for Ministry of External Affairs (EAM):
      Classify an article under this ministry **if it includes any of the following contextual relevance/references** from the points below:                                               
      - If any of the following key officials are mentioned in the article from the list `key_officials_list`, treat them as a strong signal that the associated ministry:  
        `key_officials_list = [S. Jaishankar, Kirti Vardhan Singh, Pabitra Margherita, Vikram Misri, Tanmaya Lal, Jaideep Mazumdar, Randhir Jaiswal]`
      - If any of the following keywords/phrases appear (case-insensitive) in the article from the list `keywords_phrases_list`, treat them as a strong signal that the associated ministry:  
        `keywords_phrases_list = [India's Neighbourhood, Indian Ocean Region, BIMSTEC, SAARC, G20, Consular Services, Passport Services, Visa Services, Overseas Indian Affairs, New Emerging and Strategic Technologies, Cyber Diplomacy, Public Diplomacy, SCO Summit, Voice of Global South Summits, India-CARICOM, India-SICA, ASEAN, Plurilateral, Multilateral, Bilateral, G20 Presidency, Consensus Declaration, Jan Bhagidari, Vasudhaiva Kutumbakam, SAGAR Policy, Neighbourhood First Policy, Strategic Partnerships, High-impact Grant Projects, Lines of Credit, People-to-people Ties, First Responder, Disengagements, Maritime Domain Awareness, Global Biofuels Alliance, Migration and Mobility Partnership, Asian Development Bank, Financial Stability Board, IMF, ILO, WTO, ISA, CDRI, OECD, UNWFP, ICCR, e-Vidya Bharti Portal, Passports Seva, Rules-based International Order, Global South, Supply Chain Disruptions, Disarmament, Non-Proliferation, Weapons of Mass Destruction, Cyber Dialogues, Track 1.5 Dialogue, Special Envoy, Troika, Sherpa Track, Strategic Dialogue, Pravasi Bharatiya Divas, Overseas Citizen of India, Person of Indian Origin, Defence Cooperation Agreement, Joint Military Exercise, Counter-terrorism Cooperation, Maritime Security Dialogue, Defence Attaché, Peacekeeping Operations, Military-to-Military Engagement, Bilateral Investment Treaty, Double Taxation Avoidance Agreement, Preferential Trade Agreement, Comprehensive Economic Partnership Agreement, Market Access, Tariff Concessions, Trade Facilitation, BRICS, IBSA Dialogue Forum, QUAD, East Asia Summit, ASEAN-India Summit, Shanghai Cooperation Organisation, G77, SAARC Development Fund, Extradition Treaty, Repatriation]`
      - If any of the following Policies/Schemes appear (case-insensitive) in the article from the list `Policies_schemes_list`, treat them as a strong signal that the associated ministry:  
        `Policies_schemes_list = [Indian Community Welfare Fund (ICWF), Know India Programme (KIP), e-Migrate Portal, Scholarship Programmes for Diaspora Children (SPDC), Mahatma Gandhi Pravasi Suraksha Yojana (MGPSY), Pravasi Bharatiya Bima Yojana (PBBY), Pravasi Bharatiya Divas, SAGAR Policy, Voice of Global South, Migration and Mobility Partnership, Comprehensive Economic Partnership Agreement (CEPA), Double Taxation Avoidance Agreement (DTAA), Bilateral Investment Treaty (BIT)]`
      - If any of the following Organizations appear (case-insensitive) in the article from the list `Organization_list`, treat them as a strong signal that the associated ministry:  
        `Organization_list = [Indian Council for Cultural Relations (ICCR), International Solar Alliance (ISA), Coalition for Disaster Resilient Infrastructure (CDRI), Asian Development Bank (ADB), World Trade Organization (WTO), International Monetary Fund (IMF), Organisation for Economic Co-operation and Development (OECD), United Nations World Food Programme (UNWFP)]`
      Use these signals to **classify the article under**: `"Ministry of External Affairs"`
                            
    ▶ Special Classification Rules for Ministry of Finance:
      Classify an article under this ministry **if it includes any of the following contextual relevance/references** from the points below:                                               
      - If any of the following key officials are mentioned in the article from the list `key_officials_list`, treat them as a strong signal that the associated ministry:  
        `key_officials_list = [Nirmala Sitharaman, Ajay Seth, Pankaj Chaudhary, Vumlunmang Vualnam, Arunish Chawla, Nagaraju Maddirala, K. Moses Chala, Arvind Shrivastava, V. Anantha Nageswaran]`
      - If any of the following keywords/phrases appear (case-insensitive) in the article from the list `keywords_phrases_list`, treat them as a strong signal that the associated ministry:  
        `keywords_phrases_list = [Union Budget, Fiscal Deficit, Revenue Deficit, Effective Revenue Deficit, CapEx, RE, BE, Budget Estimates, Revised Estimates, Gross Market Borrowings, Public Debt, Disinvestment, Strategic Disinvestment, Debt Sustainability, Public Account of India, Consolidated Fund of India, Contingency Fund, Outcome Budget, MTEF, Appropriation Bill, Finance Bill, Vote on Account, Token Grant, Budget Call Letter, Budget Circular, Zero-Based Budgeting, Performance-Based Budgeting, Outcome-Based Monitoring, Budget Transparency, Demand Aggregation, Modified Cash Basis of Accounting, Warrant Authority System, Audit Observations, Interest Subvention, Digital Rupee, CBDC, Unified Payments Interface, UPI, Direct Benefit Transfer, DBT, Jan Dhan, JAM Trinity, SEZ, FRBM Act, GST Council, FATF, FSAP, FSDC, IFSC, PFMS, NIP, NIIF, DIPAM, GeM, Debt Sustainability Analysis, Fiscal Slippage, Public-Private Partnership, Viability Gap Funding, India Investment Grid, Sovereign Green Bonds, Social Bonds, Green Securitization, Outcome Budget, Inclusive Development Index, BEPS, APA, MAT, TDS, STT, TCS, Income Tax Settlement Commission, Liquidity Adjustment Facility, Statutory Liquidity Ratio, Interest Liability, Monetary-Fiscal Interface, Devolution of Taxes, Fiscal Consolidation Roadmap, Deficit Financing, External Commercial Borrowings, LAF, SLR, Cash Management System, Consolidated Sinking Fund, Market Stabilization Scheme]`
      - If any of the following Policies/Schemes appear (case-insensitive) in the article from the list `Policies_schemes_list`, treat them as a strong signal that the associated ministry:  
        `Policies_schemes_list = [Stand Up India, Pradhan Mantri Garib Kalyan Yojana (PMGKY), Aam Admi Bima Yojana, Pradhan Mantri Suraksha Bima Yojana, Pradhan Mantri Jeevan Jyoti Bima Yojana (PMJJBY), Atal Pension Yojana, National Pension Scheme (NPS), Pradhan Mantri Vaya Vandana Yojana (PMVVY), Pradhan Mantri MUDRA Yojana, Pradhan Mantri Jan Dhan Yojana, Financial Sector Assessment Programme (FSAP), Credit Guarantee Scheme, Interest Subvention Scheme, Anusandhan National Research Fund, Climate Finance Taxonomy, Sustainable Securitized Debt Instruments, Equalisation Levy, E-invoicing System (GST), Counter-Cyclical Fiscal Policy, Tax Expenditure Statement, Off-Budget Borrowings, Monetized Deficit]`
      - If any of the following Organizations appear (case-insensitive) in the article from the list `Organization_list`, treat them as a strong signal that the associated ministry:  
        `Organization_list = [Department of Economic Affairs (DEA), Department of Expenditure (DoE), Department of Financial Services (DoFS), Department of Investment and Public Asset Management (DIPAM), Department of Revenue (DoR), Department of Public Enterprises (DPE), Reserve Bank of India (RBI), Central Board of Direct Taxes (CBDT), Central Board of Indirect Taxes and Customs (CBIC), Securities and Exchange Board of India (SEBI), Pension Fund Regulatory and Development Authority (PFRDA), Insurance Regulatory and Development Authority of India (IRDAI), Financial Stability and Development Council (FSDC), Financial Intelligence Unit - India (FIU-IND), Central Economic Intelligence Bureau (CEIB), Controller General of Accounts (CGA), National Investment and Infrastructure Fund (NIIF), Public Financial Management System (PFMS), National Financial Reporting Authority (NFRA)]`
      Use these signals to **classify the article under**: `"Ministry of Finance"`
                                                      
    ▶ Special Classification Rules for Ministry of Information and Broadcasting:
      Classify an article under this ministry **if it includes any of the following contextual relevance/references** from the points below:
      - If any of the following key officials are mentioned in the article from the list `key_officials_list`, treat them as a strong signal that the associated ministry:  
        `key_officials_list = [Shri Ashwini Vaishnaw, Dr. L Murugan, Shri Sanjay Jaju]`
      - If any of the following keywords/phrases appear (case-insensitive) in the article from the list `keywords_phrases_list`, treat them as a strong signal that the associated ministry:  
        `keywords_phrases_list = [Cable Television Networks (Regulation) Act 1995, Cinematograph Act 1952, Press and Registration of Periodicals Act 2023, Self-regulatory Bodies, Content Regulation, Media Ethics, Media Accreditation, Fact Checking Unit (FCU), Programme Code, Advertising Code, Emergency Alert Dissemination, Community Radio Guidelines, Digital Media Ethics Code, OTT (Over-the-top) Regularization, Broadcasting Infrastructure and Network Development (BIND) Scheme, Community Radio Station (CRS), Vartalap, Azadi Ka Amrit Mahotsav, Mann Ki Baat, Yuva Sangam, MIB – Ministry of Information and Broadcasting, CBC – Central Bureau of Communication, PIB – Press Information Bureau, NFDC – National Film Development Corporation, DFF – Directorate of Film Festivals, CBFC – Central Board of Film Certification, BECIL – Broadcast Engineering Consultants India Ltd, FTII – Film and Television Institute of India, SRFTI – Satyajit Ray Film and Television Institute, IIMC – Indian Institute of Mass Communication, EMMC – Electronic Media Monitoring Centre, CRS – Community Radio Station, DTH – Direct to Home, DRM – Digital Radio Mondiale, BIND – Broadcasting Infrastructure and Network Development, IRD – Integrated Receiver Decoder, DSNG – Digital Satellite News Gathering, National Channel, Jan Vishwas Act 2023, E-Cinepramaan, Cinematograph (Certification) Rules 2024, National Film Heritage Mission (NFHM), SHABD Initiative, Cinematograph (Amendment) Act 2023, Press and Registration of Periodicals Act 2023 (PRP Act)]`
      - If any of the following Policies/Schemes appear (case-insensitive) in the article from the list `Policies_schemes_list`, treat them as a strong signal that the associated ministry:  
        `Policies_schemes_list = [Development Communication & Information Dissemination (DCID), Development Communication & Dissemination of Filmic Content (DCDFC), Broadcasting Infrastructure Network Development (BIND), Supporting Community Radio Movement in India]`
      - If any of the following Organizations appear (case-insensitive) in the article from the list `Organization_list`, treat them as a strong signal that the associated ministry:  
        `Organization_list = [Press Information Bureau, Central Bureau Of Communication, Press Registrar General of India, Directorate of Publication Division (DPD), New Media Wing, Electronic Media Monitoring Centre (EMMC), Central Board of Film Certification, Press Council of India, Prasar Bharati, Indian Institute of Mass Communication]`
      Use these signals to **classify the article under**: `"Ministry of Information and Broadcasting"`

     ▶ Special Classification Rules for Ministry of Civil Aviation:
      Classify an article under this ministry **if it includes any of the following contextual relevance/references** from the points below:
      - If any of the following key officials are mentioned in the article from the list `key_officials_list`, treat them as a strong signal that the associated ministry:  
        `key_officials_list = [Kinjarapu Ram Mohan Naidu, General V. K. Singh, Vumlunmang Vualnam]`
      - If any of the following keywords/phrases appear (case-insensitive) in the article from the list `keywords_phrases_list`, treat them as a strong signal that the associated ministry:  
        `keywords_phrases_list = [UDAN, airport development, regional air connectivity, DGCA, Air India, Vistara, IndiGo, SpiceJet, flight safety norms, air traffic control, aviation sector growth, AAI, drone regulations, airfare caps, airline privatization, pilot licensing, civil aviation policy]`
      - If any of the following Policies/Schemes appear (case-insensitive) in the article from the list `Policies_schemes_list`, treat them as a strong signal that the associated ministry:  
        `Policies_schemes_list = [UDAN (Ude Desh ka Aam Naagrik), National Civil Aviation Policy, Drone Rules 2021, DigiYatra initiative, AirSewa grievance redressal portal]`
      - If any of the following Organizations appear (case-insensitive) in the article from the list `Organization_list`, treat them as a strong signal that the associated ministry:  
        `Organization_list = [Directorate General of Civil Aviation, DGCA, Bureau of Civil Aviation Security, BCAS, Airport Authority of India, AAI, Airports Economic Regulatory Authority, AERA, Pawan Hans Limited, Air India Asset Holding Ltd]`
      Use these signals to **classify the article under**: `"Ministry of Civil Aviation"`                                    
                                        
      Do not rely solely on the presence of keywords, official names, or predefined lists when classifying an article under a ministry. These are useful supporting signals, not definitive rules.

      Classification should be made only if the article's primary focus, intent, or policy implications clearly fall within the scope of the ministry's responsibilities — including its thematic domain, leadership role, or key initiatives.

      If the article only vaguely refers to a topic, mentions keywords incidentally, or does not clearly establish the ministry's relevance, then do not classify it under that ministry — even if signal terms appear.

      It is not necessary to classify an article into any ministry if the available information is insufficient, vague, or off-topic. Return an empty "ministries" array in such cases.

      If any news comes around bollywood, film industry, excluding legal cases against actors, do not classify them.

      If any news around cricket or other sports come which is not related to government of India, do not classify it.

      If the article has international news which is not related to India, do not classify it.


      **Return ONLY valid JSON with this exact structure and nothing else:**
      {
        "language": "...",
        "heading": "...",
        "content": "...",
        "english_heading": "...",
        "english_content": "...",
        "english_summary": "...",
        "sentiment": "positive" | "negative" | "neutral",
        "ministries": [ { "ministry": "..." } ],
        "date": "dd-mm-yyyy" | "unknown"
      }
      Ensure "ministries" is always an array, even if empty. Ensure "date" is in dd-mm-yyyy format or exactly "unknown".
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
