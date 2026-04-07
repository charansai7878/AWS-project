import boto3
import argparse
from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown

# Initialize Rich Console for better UI
console = Console()

def query_knowledge_base(knowledge_base_id, model_arn, query):
    """
    Calls the Amazon Bedrock 'retrieve_and_generate' API to get a grounded answer.
    """
    client = boto3.client("bedrock-agent-runtime")
    
    response = client.retrieve_and_generate(
        input={'text': query},
        retrieveAndGenerateConfiguration={
            'type': 'KNOWLEDGE_BASE',
            'knowledgeBaseConfiguration': {
                'knowledgeBaseId': knowledge_base_id,
                'modelArn': model_arn
            }
        }
    )
    
    return response

def display_response(response):
    """
    Extracts and displays the generated answer and citations.
    """
    output_text = response.get('output', {}).get('text', 'No answer found.')
    citations = response.get('citations', [])
    
    # Display the main answer
    console.print(Panel(Markdown(output_text), title="[bold green]Assistant Answer[/bold green]", expand=False))
    
    # Display citations if any
    if citations:
        console.print("\n[bold cyan]📚 Citations (Sources):[/bold cyan]")
        for i, cite in enumerate(citations, 1):
            for reference in cite.get('retrievedReferences', []):
                location = reference.get('location', {}).get('s3Location', {}).get('uri', 'Unknown Source')
                snippet = reference.get('content', {}).get('text', '')[:100] + "..."
                console.print(f"{i}. [blue][{location}][/blue]\n   [italic]\"{snippet}\"[/italic]")

def main():
    parser = argparse.ArgumentParser(description="Grounded Knowledge Assistant CLI")
    parser.add_argument("--kb-id", required=True, help="AWS Bedrock Knowledge Base ID")
    parser.add_argument("--model-arn", default="arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-lite-v1:0", help="Amazon Nova Model ARN")
    
    args = parser.parse_args()
    
    console.print("[bold purple]--- Grounded Knowledge Assistant ---[/bold purple]")
    console.print("Type 'exit' or 'quit' to stop.\n")
    
    while True:
        try:
            query = console.input("[bold blue]Ask a question about your documents: [/bold blue]")
            if query.lower() in ['exit', 'quit']:
                break
            
            with console.status("[bold yellow]Retrieving context and generating answer...[/bold yellow]"):
                response = query_knowledge_base(args.kb_id, args.model_arn, query)
                display_response(response)
                
        except Exception as e:
            console.print(f"[bold red]Error:[/bold red] {str(e)}")

if __name__ == "__main__":
    main()
