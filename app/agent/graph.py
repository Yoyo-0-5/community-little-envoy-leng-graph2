from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_ollama import ChatOllama
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel, Field
from typing_extensions import Literal

from app.agent.state import (
    DB_EXPERT,
    DB_EXPERT_PROMPT,
    FINISH,
    MAX_SUPERVISOR_TURNS,
    MEMBERS,
    RESEARCHER,
    RESEARCHER_PROMPT,
    ROUTES,
    SUPERVISOR_PROMPT,
    AgentState,
)
from app.config import OLLAMA_BASE_URL, OLLAMA_MODEL
from app.tools import query_community_database, tavily_tool


class RouteResponse(BaseModel):
    plan: list[Literal["DB_Expert", "Researcher"]] = Field(
        description="這次提問需要哪些專家處理，把所有需要的都列出來。"
    )
    next: Literal["FINISH", "DB_Expert", "Researcher"] = Field(
        description="下一步要喚醒的專家；plan 內的專家都回報完才能填 FINISH。"
    )


def _build_llm() -> ChatOllama:
    return ChatOllama(model=OLLAMA_MODEL, base_url=OLLAMA_BASE_URL, temperature=0)


def _as_text(content) -> str:
    """訊息內容可能是 str，也可能是 Anthropic/OpenAI 風格的多模態 block 陣列。"""
    if isinstance(content, list):
        return "".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ).strip()
    return str(content or "").strip()


def _last_ai_text(messages: list[BaseMessage]) -> str:
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            text = _as_text(message.content)
            if text:
                return text
    return ""


def _route_from_raw(raw) -> str:
    """結構化輸出解析失敗時，退而求其次從原始文字抓關鍵字。"""
    text = _as_text(getattr(raw, "content", raw)).upper()
    for route in ROUTES:
        if route.upper() in text:
            return route
    return FINISH


def _experts_reported(messages: list[BaseMessage]) -> list[str]:
    """這一輪（最後一則使用者訊息之後）已經回報過的專家。"""
    reported = []
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            break
        if isinstance(message, AIMessage) and message.name in MEMBERS:
            reported.append(message.name)
    reported.reverse()
    return reported


def _build_supervisor_node(llm: ChatOllama):
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", SUPERVISOR_PROMPT),
            MessagesPlaceholder(variable_name="messages"),
            ("system", "請先列出 plan，再決定下一個負責的專家，或回傳 FINISH。"),
        ]
    )
    router = prompt | llm.with_structured_output(RouteResponse, include_raw=True)
    members_line = "\n".join(MEMBERS)

    def supervisor_node(state: AgentState) -> dict:
        messages = state["messages"]
        # 使用者剛講完就是新的一輪：plan 與派工次數都歸零重新算。
        fresh_turn = isinstance(messages[-1], HumanMessage)
        turns = 0 if fresh_turn else state.get("supervisor_turns", 0)
        if turns >= MAX_SUPERVISOR_TURNS:
            return {"next": FINISH, "supervisor_turns": turns}

        routed = router.invoke({"messages": messages, "members": members_line})
        parsed = routed["parsed"]
        wanted = (
            [*parsed.plan, parsed.next] if parsed else [_route_from_raw(routed["raw"])]
        )

        # 與本輪舊清單取聯集，模型中途新增的專家也補派。
        plan = [] if fresh_turn else list(state.get("plan", []))
        for member in wanted:
            if member in MEMBERS and member not in plan:
                plan.append(member)

        reported = _experts_reported(messages)
        # 這輪還沒有人回報代表計畫太空，先扣住 DB_Expert，避免空手收尾。
        if not plan and not reported:
            plan = [DB_EXPERT]
        # 由程式端排隊：plan 裡還沒回報的專家依序派工，全部回報完才准 FINISH。
        pending = [member for member in plan if member not in reported]

        return {
            "next": pending[0] if pending else FINISH,
            "plan": plan,
            "supervisor_turns": turns + 1,
        }

    return supervisor_node


def _last_user_text(messages: list[BaseMessage]) -> str:
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return _as_text(message.content)
    return ""


def _build_expert_node(agent, expert_name: str):
    def expert_node(state: AgentState) -> dict:
        messages = state["messages"]
        # 只傳 messages：next / plan / supervisor_turns 不屬於專家 sub-graph 的 state schema。
        # 前一位專家回報過時，qwen 會順著上一段回答繼續寫而忘了自己的任務，補一句任務提醒拉回來。
        question = _last_user_text(messages)
        if question and not isinstance(messages[-1], HumanMessage):
            payload = {
                "messages": [
                    *messages,
                    HumanMessage(content=f"你的任務：{question}"),
                ]
            }
        else:
            payload = {"messages": messages}

        # qwen 偶爾會吐空字串，重試一次；仍為空就回報失敗字串，讓 supervisor 換人。
        for _ in range(2):
            reply = _last_ai_text(agent.invoke(payload)["messages"])
            if reply:
                break
        # 只把結論帶回主圖，中間的 tool 訊息留在 sub-graph 內，避免主圖膨脹。
        return {
            "messages": [
                AIMessage(
                    content=reply or f"{expert_name} 這次沒有產生回覆。",
                    name=expert_name,
                )
            ]
        }

    return expert_node


def build_agent():
    """Supervisor 多智能體：supervisor → 專家 → supervisor → … → END。"""
    llm = _build_llm()

    db_agent = create_react_agent(
        model=llm,
        tools=[query_community_database],
        prompt=DB_EXPERT_PROMPT,
        name=DB_EXPERT,
    )
    researcher_agent = create_react_agent(
        model=llm,
        tools=[tavily_tool],
        prompt=RESEARCHER_PROMPT,
        name=RESEARCHER,
    )

    workflow = StateGraph(AgentState)
    workflow.add_node("supervisor", _build_supervisor_node(llm))
    workflow.add_node(DB_EXPERT, _build_expert_node(db_agent, DB_EXPERT))
    workflow.add_node(RESEARCHER, _build_expert_node(researcher_agent, RESEARCHER))

    workflow.add_edge(START, "supervisor")
    workflow.add_conditional_edges(
        "supervisor",
        lambda state: state["next"],
        {DB_EXPERT: DB_EXPERT, RESEARCHER: RESEARCHER, FINISH: END},
    )
    workflow.add_edge(DB_EXPERT, "supervisor")
    workflow.add_edge(RESEARCHER, "supervisor")

    return workflow.compile(checkpointer=MemorySaver())


def build_vision_agent():
    """照片分析用的單一 react agent（吃多模態輸入，不經過 supervisor）。"""
    return create_react_agent(
        model=_build_llm(),
        tools=[query_community_database],
        checkpointer=MemorySaver(),
        prompt=(
            "你是一個具備視覺能力的社區系統助理。你會看到使用者上傳的問題照片，"
            "可查詢資料庫（cases、users、categories）輔助判斷。請依使用者指示回應。"
        ),
        name="Vision_Expert",
    )
