import os

from dotenv import load_dotenv
load_dotenv()

# --- Database ---
DB_PATH: str = os.getenv("DB_PATH", "memoryvault.db")
BUSY_TIMEOUT_MS: int = 5_000

# --- Embeddings ---
EMBEDDING_DIM: int = 768
EMBEDDING_MODEL: str = "BAAI/bge-base-en-v1.5"

# --- Confidence tiers (cosine similarity thresholds) ---
TIER_STRONG: float = 0.85
TIER_GOOD: float = 0.70
TIER_MODERATE: float = 0.65
# Below TIER_MODERATE → "Weak match"

# --- Retrieval ---
# Cosine distance is 0 (identical) to 2 (opposite). Chunks whose distance
# exceeds this threshold are considered irrelevant and dropped before the
# context is built. Lower = stricter (fewer chunks included).
RETRIEVAL_DISTANCE_THRESHOLD: float = float(os.getenv("RETRIEVAL_DISTANCE_THRESHOLD", "0.50"))
RETRIEVAL_TOP_K: int = int(os.getenv("RETRIEVAL_TOP_K", "3"))

# --- Guardian Model (Ollama, local) ---
OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
OLLAMA_TIMEOUT_SECONDS: int = int(os.getenv("OLLAMA_TIMEOUT_SECONDS", "30"))

# --- Expert Model (OpenAI GPT-4o, online) ---
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o")
OPENAI_MAX_TOKENS: int = int(os.getenv("OPENAI_MAX_TOKENS", "2048"))
EXPERT_MAX_CONTEXT_CHARS: int = int(os.getenv("EXPERT_MAX_CONTEXT_CHARS", "4000"))

# --- Web Search (Tavily) ---
TAVILY_API_KEY: str = os.getenv("TAVILY_API_KEY", "")
# Maximum search result snippets to inject per query (keeps context window lean)
TAVILY_MAX_RESULTS: int = int(os.getenv("TAVILY_MAX_RESULTS", "3"))

# --- Privacy policy defaults ---
DEFAULT_EXTERNAL_CALL: str = "deny"
RAW_PERSONAL_DATA_ONLINE: bool = False
IDENTITY_MAPPING_ONLINE: bool = False
AUDIT_ALL_ONLINE_CALLS: bool = True
PREVIEW_SENSITIVE_PAYLOADS: bool = True
FINAL_ANSWER_CHECKED_LOCALLY: bool = True

# --- Server ---
HOST: str = os.getenv("HOST", "127.0.0.1")
PORT: int = int(os.getenv("PORT", "8000"))
