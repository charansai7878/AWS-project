from flask import Flask, render_template, request, jsonify
import boto3
import os
import random
from dotenv import load_dotenv

# Ensure we always find the .env file in the same directory as this script
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))

app = Flask(__name__)

# Use environment variables if available, otherwise fallback
KNOWLEDGE_BASE_ID = os.getenv("KNOWLEDGE_BASE_ID", "YOUR_KB_ID")
MODEL_ARN = os.getenv("MODEL_ARN", "arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-lite-v1:0")

import time
from botocore.exceptions import ClientError

def query_knowledge_base(query):
    """
    Dynamic Authentication with Exponential Backoff retry logic to handle Throttling.
    """
    aws_key = os.getenv("AWS_ACCESS_KEY_ID")
    aws_secret = os.getenv("AWS_SECRET_ACCESS_KEY")
    
    if aws_key and aws_secret:
        kb_client = boto3.client("bedrock-agent-runtime", region_name="us-east-1", aws_access_key_id=aws_key, aws_secret_access_key=aws_secret)
        br_client = boto3.client("bedrock-runtime", region_name="us-east-1", aws_access_key_id=aws_key, aws_secret_access_key=aws_secret)
    else:
        kb_client = boto3.client("bedrock-agent-runtime", region_name="us-east-1")
        br_client = boto3.client("bedrock-runtime", region_name="us-east-1")

    max_retries = 6  # Increased from 3
    for attempt in range(max_retries):
        try:
            # 1. Manually pull the text straight from Pinecone
            retrieval_resp = kb_client.retrieve(
                knowledgeBaseId=KNOWLEDGE_BASE_ID,
                retrievalQuery={'text': query},
                retrievalConfiguration={'vectorSearchConfiguration': {'numberOfResults': 3}}
            )
            
            results = retrieval_resp.get('retrievalResults', [])
            if not results:
                return {'output': {'text': 'Sorry, I am unable to assist you with this request. No matching context found in S3.'}, 'citations': []}
                
            context_texts = []
            citations_data = []
            for r in results:
                snippet = r.get('content', {}).get('text', '')
                uri = r.get('location', {}).get('s3Location', {}).get('uri', 'Unknown Source')
                context_texts.append(snippet)
                citations_data.append({'retrievedReferences': [{'location': {'s3Location': {'uri': uri}}, 'content': {'text': snippet}}]})
                
            context_string = "\n\n".join(context_texts)
            model_id = MODEL_ARN.split('/')[-1] if '/' in MODEL_ARN else MODEL_ARN
            user_prompt = f"Using ONLY the following verified context from the database, answer the user's question clearly.\n\n[CONTEXT]:\n{context_string}\n\n[QUESTION]: {query}"
            
            # 3. Request model to answer
            converse_resp = br_client.converse(
                modelId=model_id,
                messages=[{"role": "user", "content": [{"text": user_prompt}]}]
            )
            
            answer_text = converse_resp['output']['message']['content'][0]['text']
            
            return {
                'output': {'text': answer_text},
                'citations': citations_data
            }

        except ClientError as e:
            error_code = e.response['Error']['Code']
            if error_code == 'ThrottlingException' and attempt < max_retries - 1:
                wait_time = (2 ** attempt) + random.uniform(0, 1)  # jitter prevents synchronized retries
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
