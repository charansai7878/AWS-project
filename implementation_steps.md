# Grounded Knowledge Assistant: Implementation Steps

This document provides a detailed breakdown of the engineering phases for building the Serverless RAG Architecture using Amazon Bedrock, S3, and Flask.

---

## 🛠️ Step 1: Local Environment Setup
Before we build, the local developer environment must be configured to communicate securely with AWS Bedrock.

*   **Python Stack**: We use Python 3.10+ for its native support for advanced AWS SDK features.
*   **Dependencies**:
    - `boto3`: The primary AWS SDK for Python.
    - `flask`: The lightweight web framework used for the client layer.
    - `python-dotenv`: Manages environment variables like `KNOWLEDGE_BASE_ID` to keep them out of source control.
*   **AWS CLI Configuration**: Authentication is handled via the AWS CLI (`aws configure`) which stores credentials in `~/.aws/credentials`.

---

## ☁️ Step 2: AWS + Pinecone Integration (Low-Cost Strategy)
This step replaces the expensive OpenSearch Serverless with **Pinecone Serverless**, which scales to $0 when not in use.

*   **S3 Data Source (AWS Console)**:
    - Create an **S3 Bucket** (e.g., `grounded-knowledge-data`) and upload your PDF/Doc documents.
*   **Vector Database Setup (Pinecone Console)**:
    - Log in to **Pinecone.io** and create a **Serverless Index**:
        - **Dimensions**: 1024 (Required for Amazon Titan Text Embeddings v2).
        - **Metric**: Cosine (Recommended for text similarity).
    - Copy your **Pinecone API Key** and your **Index Host URL**.
*   **AWS Secrets Manager Secret**:
    - Navigate to **AWS Secrets Manager**, click **Store a new secret**, choose **Other type of secret**. Ensure you store your Pinecone API Key (e.g., Key: `apiKey`, Value: your actual Pinecone API key).
    - Give it a descriptive name like `bedrock-pinecone-secret`.
*   **Custom IAM Role Creation for Bedrock**:
    - Navigate to the **IAM Console > Roles** and click **Create role**.
    - Select **Custom trust policy** and paste the following JSON to allow Bedrock to assume this role:
      ```json
      {
        "Version": "2012-10-17",
        "Statement": [ { "Effect": "Allow", "Principal": { "Service": "bedrock.amazonaws.com" }, "Action": "sts:AssumeRole" } ]
      }
      ```
    - Under **Add permissions**, assign **AdministratorAccess** (Note: Since this is for a student/personal project, this safely bypasses granular S3/SecretsManager permission errors while restricted strictly to Bedrock).
    - Name the role `Bedrock-KnowledgeBase-ManualRole` and save it.
*   **Knowledge Base Setup (AWS Console)**:
    - Navigate to **Amazon Bedrock > Knowledge Bases**.
    - Click **Create Knowledge Base**:
        - **IAM permissions**: Select **Choose an existing service role** and choose your new `Bedrock-KnowledgeBase-ManualRole`.
        - **Source**: Select your S3 bucket.
        - **Embedding Model**: Select **Amazon Titan Text Embeddings v2**. (Ensure it matches the **1024** dimensions we set up in Pinecone).
        - **Vector Store**: Select **Pinecone** instead of OpenSearch.
        - **Configuration**: Paste your **Index Host URL** (ensure no trailing slash `/`) and select the **Secrets Manager Secret** you just created.
        - **Metadata Field Mapping**: 
            - **Text field name**: Enter `text`
            - **Bedrock-managed metadata field name**: Enter `metadata`
    - **Sync**: Click **Sync** in the Bedrock Console to process documents into Pinecone Serverless.
*   **Model Access**: Note that as of recent AWS updates, Serverless Foundation Models (like Titan Text Embeddings) are **automatically enabled** on first invoke. You do not need to manually request access unless prompted to submit use case details for Claude.

---

## 💻 Step 3: Application Development (Local Flask Engine)
With the Pinecone + Bedrock integration complete, we develop the local logic to query the verified knowledge.

1.  **Credential Management**: Updated `.env` file with `KNOWLEDGE_BASE_ID` and `MODEL_ARN`.
2.  **Backend Engineering**: Developed `app_gui.py` using `boto3`. 
    - *Optimization*: Implemented a manual "Retrieve + Converse" flow in `app_gui.py` to bypass Bedrock orchestration bugs and ensure stable responses.
3.  **Secure Execution**: Configured `boto3` clients for `bedrock-agent-runtime` and `bedrock-runtime`.

---

## 🌐 Step 4: Premium Web UI Implementation
The final coding phase creates the high-end interface for the grounded assistant.

1.  **Aesthetics**: Glassmorphic dark-mode UI implemented in `static/style.css`.
2.  **Markdown Support**: Integrated `marked.js` in `templates/index.html` for rich text formatting.
3.  **Source Tracking**: Added a "Verified Sources" section to display citations for transparency.

---

## 📦 Step 5: Version Control & GitHub Integration (NEW - COMPLETED)
Moving from local development to a professional codebase management workflow.

1.  **Git Initialization**: Ran `git init` and created a `.gitignore` to protect sensitive `.env` files and cache directories.
2.  **Initial Commit**: Committed all project files (`app_gui.py`, CSS/HTML templates, etc.) locally.
3.  **GitHub Push**: Linked the local repository to [charansai7878/AWS-project](https://github.com/charansai7878/AWS-project) and pushed the `main` branch.

---

## 🚀 Step 6: EC2 Deployment Preparation (Next Step)
The system is ready to move from local hosting to the AWS Cloud.

1.  **EC2 Instance Setup**: 
    - Launch an **Ubuntu 22.04 LTS** instance.
    - **Security Group**: Allow **HTTP (80)** and **Custom TCP (5000)** for Flask.
    - **User Data Script**: Paste this into the "Advanced Details" section during launch:
      ```bash
      #!/bin/bash
      # Update and install dependencies
      apt-get update -y
      apt-get install python3-pip python3-venv git -y
      
      # Clone repository
      cd /home/ubuntu
      git clone https://github.com/charansai7878/AWS-project.git
      cd AWS-project
      
      # Setup Virtual Environment
      python3 -m venv venv
      source venv/bin/activate
      pip install -r requirements.txt
      
      # Start the app
      nohup python3 app_gui.py > log.txt 2>&1 &
      ```
2.  **IAM Role Configuration**: 
    - **A. Create IAM Policy**:
        - Go to **IAM Console > Policies > Create policy**.
        - Use the **JSON** editor and paste the policy:
          ```json
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Sid": "BedrockKnowledgeBaseAccess",
                        "Effect": "Allow",
                        "Action": ["bedrock:Retrieve"],
                        "Resource": "arn:aws:bedrock:us-east-1:*:knowledge-base/*"
                    },
                    {
                        "Sid": "BedrockModelInvocation",
                        "Effect": "Allow",
                        "Action": [
                            "bedrock:InvokeModel",
                            "bedrock:InvokeModelWithResponseStream"
                        ],
                        "Resource": "arn:aws:bedrock:us-east-1::foundation-model/*"
                    }
                ]
            }
          ```
        - Name it `EC2-Bedrock-Policy`.
    - **B. Create IAM Role**:
        - Go to **IAM Console > Roles > Create role**.
        - Select **AWS Service** and **EC2** as the use case.
        - Attach the `EC2-Bedrock-Policy` created in the previous step.
        - Name the role `EC2-Bedrock-Assistant-Role`.
    - **C. Attach to Instance**:
        - In the **EC2 Console**, select your instance.
        - Go to **Actions > Security > Modify IAM role**.
        - Select `EC2-Bedrock-Assistant-Role` and save.
    - *Security Benefit*: This allows the application to authenticate automatically without storing secret keys on the server.
3.  **Production Hardening**:
    - **Environment Variables**: Create a `.env` file on the server (manually or via User Data) containing only:
      ```bash
      KNOWLEDGE_BASE_ID=your_id_here
      MODEL_ARN=arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-lite-v1:0
      ```
    - **Update Authentication**: In `app_gui.py`, ensure the `boto3.client` calls do *not* have `aws_access_key_id` or `aws_secret_access_key` parameters. Boto3 will automatically use the IAM Instance Profile.
    - **Gunicorn (Optional)**: For stability, run with `gunicorn -w 4 -b 0.0.0.0:5000 app_gui:app`.

---

## 📈 Project Status: Ready for Deployment
The application is fully functional locally and the code is safely stored on GitHub. The final cloud deployment on EC2 using secure IAM roles is the current focus.
