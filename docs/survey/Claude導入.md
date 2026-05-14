- 権限
	- `.claude/settings.json`に書いたから同じ環境になるはず
- superpowers
	- https://qiita.com/nogataka/items/c2e73515e65533986421
	- 暴走せず、不明な部分は確認してくれる
	- 実装はsubagentにやらせ、自身はそれを取りまとめるagentになる。コンテキストが膨張するリスクが下がる
	- タスクの難易度に応じてモデルの使い分けをよしなにやってくれる
	- git worktreeも内包していて、並列作業できる
	- /pluginで検索してインストール
	- /brainstormingからはじめて、案内に従ってTDDする
		```
		  ◼ Ask clarifying questions
		  ◻ Propose 2-3 approaches
		  ◻ Present design and get approval
		  ◻ Write design spec doc
		  ◻ Spec self-review and user review
		```