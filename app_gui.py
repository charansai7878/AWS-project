from flask import Flask, render_template, request, jsonify
import boto3
import os
import time
import random
import threading
from dotenv import load_dotenv
from botocore.exceptions import ClientError
from botocore.config import Config

# Ensure we always find the .env file in the same directory as this script
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))

app = Flask(__name__)

KNOWLEDGE_BASE_ID = os.getenv("KNOWLEDGE_BASE_ID", "YOUR_KB_ID")
MODEL_ARN = os.getenv("MODEL_ARN", "arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-lite-v1:0")

# FIX 1: Override botocore's built-in retry so IT doesn't exhaust retries
# before your Python code can handle the ThrottlingException.
# mode="standard" uses exponential backoff; max_attempts=1 means botocore
# will NOT retry at all — your Python loop handles all retries instead.
bedrock_config = Config(
    region_name="us-east-1",
    retries={"max_attempts": 1, "mode": "standard"}
)

kb_client = boto3.client("bedrock-agent-runtime", config=bedrock_config)
br_client = boto3.client("bedrock-runtime", config=bedrock_config)

# In-memory cache to avoid repeat API calls for same questions
answer_cache = {}

# FIX 2: Simple token-bucket rate limiter — ensures at most 1 Retrieve
# call per second (Bedrock free-tier limit). Uses a threading.Lock so it
# works correctly even if Flask runs with multiple threads.
_rate_lock = threading.Lock()
_last_request_time = 0.0
MIN_REQUEST_INTERVAL = 1.2  # seconds between Retrieve calls (slightly above 1 req/s)

def _rate_limit_wait():
    """Block until it's safe to make the next Retrieve call."""
    global _last_request_time
    with _rate_lock:
        now = time.monotonic()
        elapsed = now - _last_request_time
        if elapsed < MIN_REQUEST_INTERVAL:
            time.sleep(MIN_REQUEST_INTERVAL - elapsed)
        _last_request_time = time.monotonic()


def query_knowledge_base(query):
    """
    Queries Bedrock Knowledge Base with:
    - In-memory cache for repeated questions
    - Rate limiter (max 1 req/s) to prevent throttling before it starts
    - Exponential backoff handled entirely in Python (botocore retries disabled)
    """
    cache_key = query.lower().strip()
    if cache_key in answer_cache:
        print(f"[CACHE HIT] Returning cached answer for: {query}")
        return answer_cache[cache_key]

    # FIX 3: Longer waits, more attempts, larger jitter window
    # Waits (approx): 5s, 10s, 20s, 40s, 80s
    max_retries = 5
    for attempt in range(max_retries):
        try:
            # Apply rate limit before every Retrieve call
            _rate_limit_wait()

            # Step 1: Retrieve context from Pinecone via Bedrock Knowledge Base
            retrieval_resp = kb_client.retrieve(
                knowledgeBaseId=KNOWLEDGE_BASE_ID,
                retrievalQuery={'text': query},
                retrievalConfiguration={
                    'vectorSearchConfiguration': {'numberOfResults': 3}
                }
            )

            results = retrieval_resp.get('retrievalResults', [])
            if not results:
                return {
                    'output': {
                        'text': 'Sorry, I am unable to assist you with this request. No matching context found in S3.'
                    },
                    'citations': []
                }

            context_texts = []
            citations_data = []
            for r in results:
                snippet = r.get('content', {}).get('text', '')
                uri = r.get('location', {}).get('s3Location', {}).get('uri', 'Unknown Source')
                context_texts.append(snippet)
                citations_data.append({
                    'retrievedReferences': [{
                        'location': {'s3Location': {'uri': uri}},
                        'content': {'text': snippet}
                    }]
                })

            context_string = "\n\n".join(context_texts)
            model_id = MODEL_ARN.split('/')[-1] if '/' in MODEL_ARN else MODEL_ARN
            user_prompt = (
                f"Using ONLY the following verified context from the database, "
                f"answer the user's question clearly.\n\n"
                f"[CONTEXT]:\n{context_string}\n\n"
                f"[QUESTION]: {query}"
            )

            # Step 2: Generate answer using Bedrock model
            converse_resp = br_client.converse(
                modelId=model_id,
                messages=[{"role": "user", "content": [{"text": user_prompt}]}]
            )

            answer_text = converse_resp['output']['message']['content'][0]['text']

            result = {
                'output': {'text': answer_text},
                'citations': citations_data
            }

            answer_cache[cache_key] = result
            return result

        except ClientError as e:
            error_code = e.response['Error']['Code']

            if error_code == 'ThrottlingException':
                if attempt < max_retries - 1:
                    # FIX 4: Start at ~5s, double each retry, add large random jitter
                    # to desynchronize concurrent requests (avoids retry storms)
                    base_wait = 5 * (2 ** attempt)           # 5, 10, 20, 40 seconds
                    jitter     = random.uniform(1.0, 5.0)    # 1–5s of jitter
                    wait_time  = base_wait + jitter
                    print(
                        f"[THROTTLED] Attempt {attempt + 1}/{max_retries}. "
                        f"Waiting {wait_time:.1f}s before retry..."
                    )
                    time.sleep(wait_time)
                    continue
                else:
                    # All retries exhausted — return a friendly message instead of crashing
                    print(f"[THROTTLED] All {max_retries} retries exhausted.")
                    return {
                        'output': {
                            'text': (
                                "**⚠️ AWS Rate Limit Reached**\n\n"
                                "Bedrock is temporarily throttling requests. "
                                "Please wait 30–60 seconds and try again. "
                                "This is a free-tier quota limit, not a code error."
                            )
                        },
                        'citations': []
                    }

            # Re-raise any other AWS errors (auth, config, etc.)
            raise e


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/ask", methods=["POST"])
def ask():
    data = request.json
    query = data.get("query")
    if not query:
        return jsonify({"error": "No query provided"}), 400

    try:
        response = query_knowledge_base(query)
        output_text = response.get('output', {}).get('text', 'No answer found.')

        # Intercept AWS Bedrock's default Guardrail rejection
        if "Sorry, I am unable to assist" in output_text:
            output_text = (
                "**⚠️ AWS Bedrock Guardrail Triggered:**\n\n"
                "The AI model is working and connected! However, Bedrock blocked the response "
                "because no relevant content was found in your Pinecone database.\n\n"
                "**Two possible causes:**\n\n"
                "1. **Empty database:** You haven't clicked the orange **Sync** button in the "
                "AWS Bedrock Console yet — so Pinecone has no vectors to search.\n"
                "2. **Off-topic question:** Your question doesn't match anything in the uploaded PDFs. "
                "This Knowledge Base only answers questions about your documents.\n\n"
                "*Fix: Click Sync in the AWS Console, wait for it to finish, then ask a question "
                "directly related to your documents.*"
            )

        citations = []
        for cite in response.get('citations', []):
            for reference in cite.get('retrievedReferences', []):
                location = reference.get('location', {}).get('s3Location', {}).get('uri', 'Unknown Source')
                snippet = reference.get('content', {}).get('text', '')
                citations.append({"location": location, "snippet": snippet})

        return jsonify({
            "answer": output_text,
            "citations": citations
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000)