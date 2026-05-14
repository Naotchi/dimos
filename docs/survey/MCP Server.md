# McpServer の使い方（概要）

  DimOS の robot skill (navigate, relative_move, person_follow, speak など) を Model Context Protocol 経由で外部に開放する HTTP サーバ（デフォルト http://localhost:9990/mcp）。

  1. 外部クライアントから robot を操作する

  Claude Code から

  claude mcp add --transport http --scope project dimos http://localhost:9990/mcp
  追加後、Claude Code のチャットからそのまま：
  - 「1 メートル前進して」
  - 「キッチンへ移動して」
  - 「この場所を desk としてタグ付けして」

  といった指示で skill が呼ばれる。

  MCP Inspector から

  npx -y @modelcontextprotocol/inspector を起動し、Transport を Streamable HTTP、URL を http://localhost:9990/mcp にして接続すると、利用可能な tool 一覧・引数スキーマ・実行結果を GUI で確認できる。デバッグや skill 動作確認に便利。

  任意の MCP 対応クライアント / 他言語から

  JSON-RPC over HTTP + SSE の標準 MCP トランスポートなので、curl, Python, TypeScript, Go など MCP SDK があればどこからでも叩ける。

  2. 内部エージェント経由で使う（音声/テキスト → LLM → skill）

  同じブループリントに同梱される McpClient が /human_input を listen しており、

  - マイク入力（STT）
  - Web 入力 (/human_input に publish するテキスト)
  - agent_send skill 経由で外から送られたテキスト

  を受けると、LLM (gpt-4o / Ollama 等) が MCP サーバ越しに適切な skill を選んで実行する。ユーザーは「自然言語で話しかける」だけで robot が動く。

  3. 起動方法

  uv run dimos run unitree-go2-agentic
  ブループリントに mcp-server (+ 任意で mcp-client) が含まれていれば自動で立ち上がる。

  4. 使いどころ

  ┌───────────────────────────────────────────────────┬────────────────────────────────┐
  │                     シナリオ                      │        使うクライアント        │
  ├───────────────────────────────────────────────────┼────────────────────────────────┤
  │ 開発者が Claude Code から robot を直接操作・検証  │ 外部 (Claude Code)             │
  ├───────────────────────────────────────────────────┼────────────────────────────────┤
  │ skill のスキーマ確認・単体テスト                  │ 外部 (MCP Inspector / curl)    │
  ├───────────────────────────────────────────────────┼────────────────────────────────┤
  │ 自社 LLM や別アプリから robot を制御              │ 外部 (任意の MCP クライアント) │
  ├───────────────────────────────────────────────────┼────────────────────────────────┤
  │ 音声・テキスト UI で robot とエンドユーザーが対話 │ 内部 (McpClient)               │
  ├───────────────────────────────────────────────────┼────────────────────────────────┤
  │ 進捗を逐次受け取りたい長時間 skill                │ SSE 購読 (外部・内部いずれも)  │
  └───────────────────────────────────────────────────┴────────────────────────────────┘

  要するに、「skill を MCP tool として 1 箇所に集約 → 人 (Claude Code) も LLM エージェントも同じ口から robot を動かせる」 という統一インターフェースとして使える。


# langchain/graphを接続できる？

● はい、できます。実際 DimOS の内部 McpClient がまさにその実装になっています。

  どう接続するか

  MCP サーバの tools/list で返るスキーマを LangChain の StructuredTool に変換 し、それを langchain.agents.create_agent(...) に渡すだけ。これで LangGraph の CompiledStateGraph が出来て、.stream({"messages": [...]}) で動かせます。

  3 つの選択肢

  A. DimOS の McpClient をそのまま使う（最短）

  ブループリントに mcp-client を入れれば、内部で LangGraph エージェントが立ち上がり、/human_input に流したテキストで skill が呼ばれる。LangChain/LangGraph を意識する必要すらない。

  B. 自前の LangChain/LangGraph アプリから繋ぐ（よくある使い方）

  公式 langchain-mcp-adapters を使うのが定番：

  from langchain_mcp_adapters.client import MultiServerMCPClient
  from langgraph.prebuilt import create_react_agent

  client = MultiServerMCPClient({
      "dimos": {"transport": "streamable_http", "url": "http://localhost:9990/mcp"}
  })
  tools = await client.get_tools()
  agent = create_react_agent("openai:gpt-4o", tools)
  これで Claude Code と同じ tool 群を、自前の LangGraph グラフのノードから呼べる。

  C. LangGraph の任意ノードに組み込む

  B で得た tools を ToolNode に入れたり、独自ノードからツール呼び出しするだけ。StateGraph 内のどこに置いてもよい。

  注意点

  - DimOS の MCP サーバは Streamable HTTP トランスポート。stdio ではないので、adapter 側でも streamable_http を指定すること。
  - 進捗通知 (notifications/progress) は SSE で流れてくるので、長時間 skill の途中経過を LangGraph 側で受けたい場合は adapter のストリーム対応版を使うか、別途 SSE を購読する。
  - 認証は無いので、外部公開する場合は前段にプロキシを置く。

  要するに 「MCP 対応の LangChain アダプタを噛ませれば、好きな LangGraph グラフから DimOS の skill を tool として呼べる」 という素直な構成です。