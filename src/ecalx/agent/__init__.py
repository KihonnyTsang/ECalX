"""M4-E3 single Research Agent and deterministic scientific capabilities."""
from .runtime import ResearchAgent
from .llm import configured_client, OpenAIResponsesClient, DeterministicClient, ScriptedClient
from .siliconflow import SiliconFlowChatCompletionsClient

__all__ = ['ResearchAgent','configured_client','OpenAIResponsesClient','SiliconFlowChatCompletionsClient','DeterministicClient','ScriptedClient']
