from typing import Annotated

from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

FINISH = "FINISH"
DB_EXPERT = "DB_Expert"
RESEARCHER = "Researcher"
MEMBERS = (DB_EXPERT, RESEARCHER)
ROUTES = (FINISH, *MEMBERS)

# Supervisor 最多派工幾次就強制收尾，避免模型一直指派、對話卡住。
MAX_SUPERVISOR_TURNS = 4


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    # Supervisor 決定的下一個節點（值只能是 ROUTES 之一），conditional edges 靠它分流。
    next: str
    # 本輪要派工的專家清單，Supervisor 每輪重算後與舊清單取聯集，避免漏派。
    plan: list[str]
    # 累計派工次數，做迴圈上限保險。
    supervisor_turns: int


SUPERVISOR_PROMPT = """你是一位監督者（Supervisor），只負責分派工作，自己不查資料、不回答問題。
你管理的專家如下：
{members}
- DB_Expert：查詢社區內部資料庫（cases 案件紀錄、users 使用者），撰寫 PostgreSQL SQL。
- Researcher：透過 Tavily 聯網搜尋取得外部即時資訊與常識。

分派規則：
1. 需要社區內部資料（案件、用戶）→ DB_Expert。
2. 需要外部即時資訊或常識補充 → Researcher。
3. 使用者一次問到不只一件事時（例如既要社區內部資料、又要外部知識），plan 要把兩個專家都列進去，缺一個就不能 FINISH。
4. 先輸出 plan（這次提問需要哪些專家全部列出），再輸出 next。
5. 絕對不要自己動手查資料，也不要讓同一個專家重複做同一件事。"""

DB_EXPERT_PROMPT = """你是資料庫專家，負責撰寫 PostgreSQL SQL 查詢 community_envoys 資料庫。
可用資料表：cases（案件紀錄）、users（使用者）、categories（類別字典）。
要求：
1. 只讀取資料（SELECT），不可 INSERT / UPDATE / DELETE / DROP。
2. 欄位不確定就先查 information_schema.columns，絕對不要猜欄位名。
3. 一次查完，回報時用中文條列重點（筆數、關鍵欄位），並附上你實際執行的 SQL。
4. 查不到資料要明說「查不到」，不要編造。
5. 你只負責交辦給你的部分，不要順手回答使用者問的其他部分。"""

RESEARCHER_PROMPT = """你是搜尋專家，負責透過 Tavily 聯網搜尋取得最新的外部資訊。
要求：
1. 查詢時用繁體中文關鍵字，一次最多三個查詢詞。
2. 回報時用中文條列重點，並標註資訊來源。
3. 搜不到就明說「網路上查不到」，不要編造。
4. 你只負責交辦給你的部分，不要順手回答使用者問的其他部分。"""
