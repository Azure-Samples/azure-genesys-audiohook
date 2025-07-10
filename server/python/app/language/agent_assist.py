import os

import yaml
from semantic_kernel.connectors.ai.open_ai import AzureChatCompletion
from semantic_kernel.contents import ChatHistorySummarizationReducer
from semantic_kernel.kernel import Kernel


class AgentAssistant:
    """Agent Assist maintains conversational context and create summary, and performs RAG against a user-supplied domain knowledge base."""

    def __init__(self, config_path: str):
        with open(config_path) as f:
            self.config = yaml.safe_load(f)

        # Load configuration from environment
        self.aoai_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
        self.aoai_deployment = os.getenv("AZURE_OPENAI_MODEL_DEPLOYMENT")
        self.aoai_key = os.getenv("AZURE_OPENAI_KEY")

        self.kernel = self.initialize_kernel()
        self.reducer = ChatHistorySummarizationReducer(
            service=self.kernel.get_service(service_id="chat-completion"),
            target_count=self.config.get("reducer_threshold", 5),
            auto_reduce=True,
        )
        self.reducer.add_system_message("""You are an Agent Assist who receives transcription from both Agent and Customer.
        Your task:
        - Identify issues, resolutions, and strong customer sentiments from the conversation.
        - Generate a concise summary.
        - Then provide a private suggestion to the Agent only.

        Do NOT simulate customer responses or continue the dialogue.
        Do NOT suggest what the customer might say next.
        """)
        self.message_buffer = []

    def initialize_kernel(self):
        kernel = Kernel()
        kernel.add_service(
            AzureChatCompletion(
                deployment_name=self.aoai_deployment,
                api_key=self.aoai_key,
                endpoint=self.aoai_endpoint,
                service_id="chat-completion",
            )
        )
        return kernel

    async def on_transcription(self, fragment: str) -> str | None:
        self.message_buffer.append(fragment)

        if len(self.message_buffer) < self.config["summary_interval"]:
            print(f"current buffer size {len(self.message_buffer)}")
            return None  # Not ready yet

        return await self.invoke_llm()

    async def flush_summary(self) -> str | None:
        result = None
        if self.message_buffer:
            result = await self.invoke_llm()

        # Optionally, print final context
        # print('\n@ Stored messages - ')
        # for msg in self.reducer.messages:
        #     print(f"{msg.role} - {msg.content}")

        return result

    async def invoke_llm(self) -> str | None:
        # Build user input
        user_input = "Transcriptions:\n" + " ".join(self.message_buffer)
        print(f"input {user_input}")
        self.message_buffer.clear()

        self.reducer.add_user_message(user_input)

        reinforce_prompt = """
            {{$chat_history}}
            {{$user_input}}

            Respond in the following format:

            Issue and Customer Sentiment:
            [summary here]

            Suggestion:
            [suggestion here]
            """
        response = await self.kernel.invoke_prompt(
            prompt=reinforce_prompt, user_input=user_input, chat_history=self.reducer
        )
        if response:
            self.reducer.add_message(response.value[0])
            print(f"response: {response.value[0]} \n")
            return response.value[0]
        return None
