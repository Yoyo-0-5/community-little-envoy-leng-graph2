from langchain_ollama import ChatOllama
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import create_react_agent

from app.agent.state import SYSTEM_PROMPT
from app.config import OLLAMA_BASE_URL, OLLAMA_MODEL
from app.tools import tools


def build_agent():
    llm = ChatOllama(
        model=OLLAMA_MODEL,
        base_url=OLLAMA_BASE_URL,
        temperature=0,
    )
    memory = MemorySaver()
    return create_react_agent(
        model=llm,
        tools=tools,
        checkpointer=memory,
        prompt=SYSTEM_PROMPT,
    )