from langchain_tavily import TavilySearch

from app.config import TAVILY_API_KEY

tavily_tool = TavilySearch(max_results=3, api_key=TAVILY_API_KEY)