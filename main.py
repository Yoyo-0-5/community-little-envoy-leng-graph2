import argparse

from app.agent.graph import build_agent


def main() -> None:
    parser = argparse.ArgumentParser(description="社區小尖兵 AI Agent")
    parser.add_argument(
        "--thread",
        default="community_session_01",
        help="對話 thread_id，確保多輪對話能串接 (預設: community_session_01)",
    )
    args = parser.parse_args()

    print("正在初始化 Agent（載入 Ollama 模型與工具）...")
    app = build_agent()
    config = {"configurable": {"thread_id": args.thread}}

    print("社區小尖兵 AI Agent 已啟動！（輸入 'exit' 結束對話）")
    while True:
        user_input = input("\n使用者: ")
        if user_input.lower() in ["exit", "quit"]:
            break

        print("Agent 思考與執行中...\n")
        for chunk in app.stream(
            {"messages": [("user", user_input)]},
            config=config,
            stream_mode="values",
        ):
            message = chunk["messages"][-1]
            message.pretty_print()


if __name__ == "__main__":
    main()