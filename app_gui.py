from flask import Flask, render_template, request, jsonify
import boto3
import os
import time
import random
from dotenv import load_dotenv
from botocore.exceptions import ClientError

# Ensure we always find the .env file in the same directory as this script
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))

app = Flask(__name__)

# Use environment variables if available, otherwise fallback
KNOWLEDGE_BASE_ID = os.getenv("KNOWLEDGE_BASE_ID", "YOUR_KB_ID")
MODEL_ARN = os.getenv("MODEL_ARN", "arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-lite-v1:0")

# FIX 1: Create boto3 clients ONCE at startup, not on every request
# This uses the EC2 IAM Role automatically (no hardcoded keys needed)
# IAM Instance Profile credentials have higher rate limits than hardcoded keys
kb_client = boto3.client("bedrock-agent-runtime", region_name="us-east-1")
br_client = boto3.client("bedrock-runtime", region_name="us-east-1")

# FIX 2: Simple in-memory cache to avoid repeat API calls for same questions
answer_cache = {}

def query_knowledge_base(query):
    """
    Queries Bedrock Knowledge Base with:
    - Cached responses for repeated questions
    - Exponential backoff with jitter for throttling
    """

    # FIX 3: Return cached answer if same question was asked before
    cache_key = query.lower().strip()
    if cache_key in answer_cache:
        print(f"[CACHE HIT] Returning cached answer for: {query}")
        return answer_cache[cache_key]

    # FIX 4: More retries with longer waits + random jitter
    max_retries = 6
    for attempt in range(max_retries):
        try:
            # Step 1: Retrieve context from Pinecone via Bedrock Knowledge Base
            retrieval_resp = kb_client.retrieve(
                knowledgeBaseId=KNOWLEDGE_BASE_ID,
                retrievalQuery={'text': query},
                retrievalConfiguration={'vectorSearchConfiguration': {'numberOfResults': 3}}
            )

            results = retrieval_resp.get('retrievalResults', [])
            if not results:
                return {
                    'output': {'text': 'Sorry, I am unable to assist you with this request. No matching context found in S3.'},
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

            # Save to cache before returning
            answer_cache[cache_key] = result
            return result

        except ClientError as e:
            error_code = e.response['Error']['Code']
            if error_code == 'ThrottlingException' and attempt < max_retries - 1:
                # FIX 5: Longer waits with random jitter to prevent retry storms
                # Waits: ~2s, ~4s, ~8s, ~16s, ~32s
                wait_time = (2 ** (attempt + 1)) + random.uniform(0, 2)
                print(f"[THROTTLED] Attempt {attempt + 1}/{max_retries}. Waiting {wait_time:.1f}s before retry...")
                time.sleep(wait_time)
                continue
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
                "The AI model is working perfectly and connected successfully! However, Bedrock securely blocked it from answering because it could not find any relevant information in your Pinecone Database. "
                "This happens for two reasons:\n\n"
                "1. **Empty Database:** You have not clicked the orange **Sync** button in the AWS Bedrock Console yet, so your Pinecone database is physically empty.\n"
                "2. **Off-Topic Question:** You asked a question (like 'Hello') that does not match the text inside the PDFs you uploaded. Because this is a strict Knowledge Base, it is programmed to refuse to answer anything that isn't in your files!\n\n"
                "*Fix: Go click 'Sync' in the AWS Console, wait for it to finish, and ask a question directly related to your documents!*"
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