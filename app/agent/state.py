from typing import Annotated

from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]


SYSTEM_PROMPT = """你是一個專業的社區系統助理。你可以使用以下兩種工具來回答使用者的問題：
1. 查詢資料庫：尋找社區內部的案件(cases)或使用者(users)紀錄。
2. 聯網搜尋：當遇到需要外部即時資訊或常識補充時使用。
請判斷使用者的意圖，選擇合適的工具來完成任務。"""