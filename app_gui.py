from flask import Flask, render_template, request, jsonify
import boto3
import os
import time
import random
import threading
import json
from pathlib import Path
from dotenv import load_dotenv
from botocore.exceptions import ClientError
from botocore.config import Config

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))

app = Flask(__name__)

KNOWLEDGE_BASE_ID = os.getenv("KNOWLEDGE_BASE_ID", "YOUR_KB_ID")
MODEL_ARN = os.getenv("MODEL_ARN", "arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-lite-v1:0")

# ── boto3 clients ──────────────────────────────────────────────────────────────
# max_attempts=1 disables botocore's own hidden retry loop entirely.
# YOUR code below is the only thing that retries — no more "reached max retries: 4".
bedrock_config = Config(
    region_name="us-east-1",
    retries={"max_attempts": 1, "mode": "standard"}
)
kb_client = boto3.client("bedrock-agent-runtime", config=bedrock_config)
br_client = boto3.client("bedrock-runtime",       config=bedrock_config)

# ── Persistent disk cache ──────────────────────────────────────────────────────
# Saves answers to a JSON file so they survive server restarts.
# On a free-tier account this is critical — every cache hit = one less throttle.
CACHE_FILE = Path(BASE_DIR) / ".answer_cache.json"

def _load_cache():
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text())
        except Exception:
            pass
    return {}

def _save_cache(cache: dict):
    try:
        CACHE_FILE.write_text(json.dumps(cache, indent=2))
    except Exception as e:
        print(f"[CACHE] Failed to save: {e}")

answer_cache: dict = _load_cache()
print(f"[CACHE] Loaded {len(answer_cache)} cached answers from disk.")

# ── Request serialiser ─────────────────────────────────────────────────────────
# KEY FIX: A semaphore that allows only 1 Retrieve call at a time.
# If two users ask simultaneously the second one waits instead of both
# hitting Bedrock at once and both getting throttled.
_retrieve_semaphore = threading.Semaphore(1)

# Minimum gap between consecutive Retrieve calls (seconds).
# Bedrock free-tier quota is typically 1-5 req/min.
# 15 seconds = ~4 req/min — safely under the limit.
MIN_INTERVAL_SECONDS = 15.0
_last_retrieve_time  = 0.0


def _throttle_wait():
    """Sleep until MIN_INTERVAL_SECONDS has passed since the last Retrieve call."""
    global _last_retrieve_time
    elapsed = time.monotonic() - _last_retrieve_time
    if elapsed < MIN_INTERVAL_SECONDS:
        sleep_for = MIN_INTERVAL_SECONDS - elapsed
        print(f"[RATE LIMITER] Waiting {sleep_for:.1f}s before next Retrieve call...")
        time.sleep(sleep_for)
    _last_retrieve_time = time.monotonic()


# ── Core query function ────────────────────────────────────────────────────────
def query_knowledge_base(query: str) -> dict:
    cache_key = query.lower().strip()

    # 1. Return from cache immediately — no API call needed
    if cache_key in answer_cache:
        print(f"[CACHE HIT] '{query[:60]}'")
        return answer_cache[cache_key]

    # 2. Serialise: only one request goes to Bedrock at a time
    with _retrieve_semaphore:
        # Double-checked locking: another thread may have cached this while we waited
        if cache_key in answer_cache:
            return answer_cache[cache_key]

        max_retries = 3          # fewer retries — each wait is long enough
        last_error  = None

        for attempt in range(max_retries):
            try:
                # Enforce minimum interval between calls
                _throttle_wait()

                # ── Step 1: Retrieve context vectors from Pinecone ──
                print(f"[RETRIEVE] Attempt {attempt + 1}/{max_retries} for '{query[:60]}'")
                retrieval_resp = kb_client.retrieve(
                    knowledgeBaseId=KNOWLEDGE_BASE_ID,
                    retrievalQuery={"text": query},
                    retrievalConfiguration={
                        "vectorSearchConfiguration": {"numberOfResults": 3}
                    },
                )

                results = retrieval_resp.get("retrievalResults", [])
                if not results:
                    return {
                        "output": {"text": (
                            "No matching content found in the knowledge base. "
                            "Make sure you have clicked **Sync** in the AWS Bedrock Console "
                            "and that your question relates to the uploaded documents."
                        )},
                        "citations": [],
                    }

                context_texts  = []
                citations_data = []
                for r in results:
                    snippet = r.get("content", {}).get("text", "")
                    uri     = r.get("location", {}).get("s3Location", {}).get("uri", "Unknown Source")
                    context_texts.append(snippet)
                    citations_data.append({
                        "retrievedReferences": [{
                            "location": {"s3Location": {"uri": uri}},
                            "content":  {"text": snippet},
                        }]
                    })

                # ── Step 2: Generate answer with Nova Lite ──
                context_string = "\n\n".join(context_texts)
                model_id = MODEL_ARN.split("/")[-1] if "/" in MODEL_ARN else MODEL_ARN
                user_prompt = (
                    "Using ONLY the following verified context from the database, "
                    "answer the user's question clearly.\n\n"
                    f"[CONTEXT]:\n{context_string}\n\n"
                    f"[QUESTION]: {query}"
                )

                converse_resp = br_client.converse(
                    modelId=model_id,
                    messages=[{"role": "user", "content": [{"text": user_prompt}]}],
                )
                answer_text = converse_resp["output"]["message"]["content"][0]["text"]

                result = {
                    "output":    {"text": answer_text},
                    "citations": citations_data,
                }

                # Persist to cache
                answer_cache[cache_key] = result
                _save_cache(answer_cache)
                return result

            except ClientError as e:
                error_code = e.response["Error"]["Code"]
                last_error = e

                if error_code == "ThrottlingException":
                    if attempt < max_retries - 1:
                        # Back off for 60s + jitter — this is a quota issue,
                        # not a transient spike. Short waits do not help.
                        wait = 60 + random.uniform(5, 15)
                        print(f"[THROTTLED] Attempt {attempt + 1}. Waiting {wait:.0f}s...")
                        time.sleep(wait)
                        continue
                    else:
                        # All retries exhausted — return a user-friendly message
                        return {
                            "output": {"text": (
                                "**AWS Bedrock quota temporarily exhausted**\n\n"
                                "Your account's free-tier request quota (typically 1-5 req/min) "
                                "has been reached. This is not a code error.\n\n"
                                "**What to do:**\n"
                                "- Wait **2-3 minutes** and ask your question again.\n"
                                "- Or go to **AWS Console -> Service Quotas -> Amazon Bedrock** "
                                "and request an increase for *Retrieve requests per minute*.\n\n"
                                "Tip: repeated questions are cached, so asking the same "
                                "question again after the quota resets costs zero extra calls."
                            )},
                            "citations": [],
                        }
                raise  # re-raise non-throttling errors immediately

        raise last_error


# ── Flask routes ───────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/ask", methods=["POST"])
def ask():
    data  = request.json
    query = (data or {}).get("query", "").strip()
    if not query:
        return jsonify({"error": "No query provided"}), 400

    try:
        response    = query_knowledge_base(query)
        output_text = response.get("output", {}).get("text", "No answer found.")

        # Intercept Bedrock's generic guardrail rejection message
        if "Sorry, I am unable to assist" in output_text:
            output_text = (
                "**Bedrock returned no answer**\n\n"
                "The model found no relevant content in your Pinecone database.\n\n"
                "**Two common causes:**\n"
                "1. **Sync not done** - click the orange **Sync** button in the "
                "AWS Bedrock Knowledge Base console and wait for it to finish.\n"
                "2. **Off-topic question** - your question does not match any content "
                "in the uploaded documents. Try asking something directly from your files."
            )

        citations = []
        for cite in response.get("citations", []):
            for ref in cite.get("retrievedReferences", []):
                citations.append({
                    "location": ref.get("location", {}).get("s3Location", {}).get("uri", "Unknown"),
                    "snippet":  ref.get("content", {}).get("text", ""),
                })

        return jsonify({"answer": output_text, "citations": citations})

    except Exception as e:
        print(f"[ERROR] {e}")
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)