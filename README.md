# 📘 Grounded Knowledge Assistant

Building Private-Data Intelligence with Zero-Server RAG Architecture.

## 🚀 Setup Instructions

### 1. ☁️ AWS Resource Creation (Stage I - III)
1. **S3 Bucket:** Create a bucket (e.g., `grounded-knowledge-data`) and upload your PDFs.
2. **Bedrock Knowledge Base:**
    - Go to **Amazon Bedrock > Knowledge Bases**.
    - Click **Create knowledge base**. Use "Quick create" for OpenSearch Serverless.
    - Set the **S3 bucket** created above as the Data Source.
    - Use **Titan Text Embeddings v2** as the model.
3. **Model Access:** Ensure you have access to **Claude 3 Haiku** in your region under **Amazon Bedrock > Model access**.
4. **Sync:** After creating the KB, click **Sync** in the Data sources tab.

### 2. 💻 Local Setup
1. **Python Environment:**
   ```bash
   pip install -r requirements.txt
   ```
2. **AWS Credentials:**
   Ensure your local machine is authenticated:
   ```bash
   aws configure
   ```

### 3. 🏃‍♂️ Run the Chat Assistant
Get your `Knowledge Base ID` from the Bedrock console and run:

```bash
python app.py --kb-id YOUR_KB_ID
```

## 🧠 Why This Project?
- **No Hallucinations:** Answers are strictly grounded in your context.
- **Citations included:** Every answer provides the source S3 URI.
- **Cost-Efficient:** Uses Claude 3 Haiku for the "Budget-King" GenAI experience.

---
*Created as part of the Zero-Server RAG Architecture project.*
