# blueprint / profile / env 責務分離 設計

`docs/env-vs-config.md`（env vs config の A/B/C 分類）に **blueprint 軸**を加えて完成させる設計。
profile 名に畳み込まれていた複数の独立軸を分解し、衝突を precedence で裁くのではなく
**設計で消す**ことを目的とする。

- 前提ドキュメント: `docs/env-vs-config.md`（category A/B/C と precedence）
- 関連: `docs/superpowers/specs/2026-05-19-profile-and-env-config-policy-design.md`（profile 機構）、
  `docs/superpowers/specs/2026-05-21-unify-bench-and-profile-config-design.md`（bench 統合）

## 動機

現状 7 つの profile は、profile 名 1 つに **4 つの独立した軸**を畳み込んでいる:

```
local-qwen-voicevox - spark - detection - sim
└─ backend/model ──┘  └hw─┘   └capability┘  └run-mode┘
```

実データで確認した畳み込みの正体:

- `-sim` 系（3 profile）は唯一の差が `g.simulation` の true/false。pure な複製。
- `-detection` 系は config.json 上の差が `timedmcpclient.model` だけ（`qwen3.6-35b-a3b` = VL モデル）。
  物体検知そのものは config.json に現れず、blueprint（`Detection3DModule` の wiring）の責務。
- `spark` と `desktop` の config.json（category A）は**バイト一致**。唯一の差は `.env` の endpoint。

→ profile 名が blueprint 選択・run-mode・machine を兼任しており、組合せ爆発と責務の混線を生んでいる。

## スコープ

### In scope（すべて fork-local ファイル）

- `dimos/robot/unitree/go2/blueprints/agentic/unitree_go2_agentic_local_tts.py`（model 焼き込み廃止）
- `dimos/agents/mcp/mcp_client_ja.py`（`TimedMcpClient` に category-A の `model` Field default 追加）
- `dimos/agents/llm_env_ja.py`（`resolve_llm_model` から model 解決の責務を外し、endpoint mirroring に純化）
- `configs/profiles/`（7→3 へ再編、命名変更、`g` ブロック除去）
- `scripts/bench_llm.py` + `scripts/bench_configs/*.yaml`（bench の sim 供給を YAML フィールド化）

### Out of scope

- **`g` マージ不全**（`dimos/robot/cli/dimos.py:294` の `kwargs["g"] = cli_config_overrides` が
  config.json の `g` を merge せず置換する footgun）。修正は **upstream 由来 `dimos/robot/cli/dimos.py`** の
  編集になるため fork ポリシーで除外。本設計では `g.simulation` が profile config.json から除去されるため、
  この footgun を**踏まなくなる**（config.json に `g` が無い → CLI フラグが clobber する対象が無い）。
- `worker_manager_python.py` の precedence (`kwargs.update`) は **触らない**。model 焼き込みを廃止することで、
  fork が upstream のこの実装詳細に依存しなくなる（追従リスク低下）。

## 責務モデル（3 軸 + run-mode + precedence）

| 軸 | 所有する concern | 持たない | 選択方法 | git |
|---|---|---|---|---|
| **blueprint**（コード） | トポロジ: module 構成・transport・remapping・disabled。capability の有無（detection wiring 等） | model / endpoint / run-mode | `dimos run <bp>` 位置引数 | コード |
| **profile / config.json**（category A） | デプロイ調整値: `timedmcpclient.model`（自由切替）, `mic_mode`, whisper params, tts impl, memory_limit | secret / run-mode | `--profile NAME` | 管理 |
| **profile / .env**（category B/C） | secret + endpoint（`DIMOS_LLM_BASE_URL/KEY` → `OPENAI_*` mirror）。**machine（spark/desktop）の本質はここ** | category A の値 | profile 同梱（実体は gitignore、`.env.example` のみ commit） | 除外 |
| **run-mode（`g.*` / invocation）** | `simulation` 等。profile でも blueprint でもない invocation パラメータ | — | `dimos run --simulation` / bench YAML `simulation:` | 起動記録に残る |

### 核心の主張

責務分離は「値をどのファイルに置くか」ではなく **「誰が所有し、衝突時どれが勝つか」**。
そして本設計の方針は **precedence に頼らず、衝突源を設計で消す**こと。

precedence は衝突を裁くルールではなく、衝突を消したあとの**フォールバック順**に格下げする:

```
-o/--option  >  profile config.json  >  GlobalConfig field default(env seed)  >  hardcoded default
```

## 設計詳細

### 1. model の衝突を設計で消す

**現状の衝突（blueprint vs profile）:**

- blueprint: `unitree_go2_agentic_local_tts.py` が import 時に
  `_LLM_MODEL = resolve_llm_model()`（env `DIMOS_LLM_MODEL`、default `gpt-4o`）を評価し、
  `TimedMcpClient.blueprint(model=_LLM_MODEL)` で atom kwargs に**焼き込む**。
- profile: config.json の `timedmcpclient.model` が**同じフィールド**を runtime で渡す。
- 両者の衝突は `worker_manager_python.py:158` の `kwargs.update(blueprint_args...)`（config.json 後勝ち）に
  **暗黙に救われている**だけ。これは `env-vs-config.md` の anti-pattern 3「シードを 2 箇所で読む」に該当。

**修正（model の書き手を profile config.json に一本化）:**

```python
# llm_env_ja.py: resolve_llm_model() を 2 つの責務に分割。
#   - endpoint mirroring（DIMOS_LLM_BASE_URL/KEY → OPENAI_*）= category B/C wiring。【存続】
#   - model 文字列の解決・返却。【廃止】
# 副作用専用の関数に純化（model は返さない）:
def mirror_llm_endpoint_env() -> None:
    base_url = os.environ.get("DIMOS_LLM_BASE_URL")
    api_key = os.environ.get("DIMOS_LLM_API_KEY")
    if base_url:
        os.environ["OPENAI_BASE_URL"] = base_url
    if api_key:
        os.environ["OPENAI_API_KEY"] = api_key

# unitree_go2_agentic_local_tts.py
#   _LLM_MODEL = resolve_llm_model()                                  ← 削除
#   TimedMcpClient.blueprint(model=_LLM_MODEL, system_prompt=...)     ← model= を渡さない
#
#   endpoint mirroring は import 時（main プロセス、apply_profile 後・worker fork 前）に
#   呼ぶ必要があるため、副作用呼び出しだけ残す:
mirror_llm_endpoint_env()
TimedMcpClient.blueprint(system_prompt=SYSTEM_PROMPT_JA)

# mcp_client_ja.py の TimedMcpClient config に category-A の Field default を持たせる
#   (env-vs-config.md の "explicit config > env seed > Field default" パターン)
#   default_factory は worker で config 生成時に DIMOS_LLM_MODEL を seed として読む
#   (DIMOS_LLM_MODEL は apply_profile の load_dotenv → forkserver 経由で worker に継承される)
model: str = Field(
    default_factory=lambda: os.environ.get("DIMOS_LLM_MODEL", "gpt-4o")
)
```

- これで `timedmcpclient.model` の書き手は **profile config.json のみ**（無指定時は env seed → `gpt-4o`）。
- blueprint はもう model を書かないので **衝突が消え**、precedence は model に関して non-load-bearing になる。
- endpoint mirroring（category B/C wiring）は `mirror_llm_endpoint_env()` として import 時に存続。
  **model 解決の責務だけ**を `resolve_llm_model` から外す。
- **副次効果:** `bench_llm.py` が「blueprint の import を apply_profile 後まで遅延する」フラジャイルな
   workaround（resolve_llm_model が import 時に env を読むため）が**不要**になる。
   ※ ただし `bench_llm.py` も同様に endpoint mirroring を apply_profile 後・build 前に呼ぶ必要がある点に注意
   （blueprint import 経由 or 明示呼び出しのいずれかで担保）。

> 補足: 「detection blueprint には VL モデルの profile を当てる」は **enforce しない運用規約**。
> blueprint に modality requirement を宣言させる/resolver で選ばせる機構は **作らない**（YAGNI）。
> 操作者が `--profile qwen-vl` を detection blueprint に手で組み合わせる。

### 2. run-mode（simulation）を invocation 側へ

**確認済みの事実:** `g.simulation` は blueprint を変えない。`global_config.py` の
`unitree_connection_type` property が `simulation=True` で `"mujoco"`、それ以外で `"webrtc"` を返し、
**同一 blueprint** が runtime に接続 factory を切り替える。モジュール側も `self.config.g.simulation` を
読んで微調整するだけ（例 `type/map.py:62`）。よって sim は profile に持つ必要のない invocation パラメータ。

**変更:**

- profile config.json から `g` ブロックを除去（`g.simulation` は profile が持たない）。
- `dimos run`: 既存の `--simulation` フラグで供給（upstream 由来、**触らない**）。
- bench: `dimos run` を通らないため、bench YAML に **top-level `simulation: true`** フィールドを追加し、
  `bench_llm.py` が `kwargs.setdefault("g", {})["simulation"] = cfg.get("simulation", False)` で注入。
  bench の再現性は commit される bench YAML に sim が残ることで維持。

### 3. machine（spark/desktop）を env へ / profile を 7→3 へ再編

machine の本質（endpoint）は `.env`（category B/C）。`.env` は gitignore で `.env.example` のみ commit
されるため、**1 つの profile を各マシンで `.env` を埋めて使う**のが自然。machine は profile 名に持たない。

profile は **category A の中身（= どの LLM か）でのみ**分ける:

| 新 profile | config.json で区別される中身 | .env（マシンごとに記入） | 旧 profile からの移行元 |
|---|---|---|---|
| `qwen-text` | model = `openai:qwen/qwen3-30b-a3b-2507`（text） | local OpenAI 互換 endpoint | local-qwen-voicevox(-sim), -spark(-sim) |
| `qwen-vl` | model = `openai:qwen/qwen3.6-35b-a3b`（VL, detection 用） | local OpenAI 互換 endpoint | -spark-detection(-sim) |
| `gpt4o` | model = `openai:gpt-4o`（VL） | azure cloud endpoint + key | azure-gpt4o-voicevox-sim |

3 profile 共通の category A: `mic_mode=vad`, whisper `large-v3`/`fp16`, tts `voicevox`/streaming。

**起動マトリクス（直交化後）:**

```
# plain agentic, sim
dimos run unitree-go2-agentic-local-tts           --profile qwen-text --simulation
# plain agentic, real
dimos run unitree-go2-agentic-local-tts           --profile qwen-text
# detection, sim
dimos run unitree-go2-agentic-local-tts-detection --profile qwen-vl   --simulation
# detection, real
dimos run unitree-go2-agentic-local-tts-detection --profile qwen-vl
# detection, azure, sim
dimos run unitree-go2-agentic-local-tts-detection --profile gpt4o     --simulation
```

### 4. memory_limit の扱い（要決定 → 決定）

現状 `rerunbridgemodule.memory_limit` は detection-**real** のみ `32GB`、他は `4GB`。sim をフラグ化すると
`qwen-vl` profile は memory_limit を 1 つに決める必要がある（real は 32GB、sim は 4GB を望んでいた）。

**決定:** 全 profile で `rerunbridgemodule.memory_limit` を **`"25%"`**（マシン適応の割合指定。rerun-bridge の
デフォルトと同じ）にし、4GB/32GB の分岐自体を消す。絶対値で固定したい個別ケースは
`-o rerunbridgemodule.memory_limit=...` で runtime override する。

## Migration

1. blueprint / TimedMcpClient / resolve_llm_model を §1 のとおり修正。
2. `configs/profiles/` を §3 のとおり再編: `qwen-text` / `qwen-vl` / `gpt4o` を作成、
   旧 7 profile を削除。各 profile の config.json から `g` ブロックを除去、memory_limit を `"25%"` に。
   `.env.example` を新 profile に用意（local 用 / azure 用）。
3. `scripts/bench_configs/whisper-largev3-qwen3-30b-a3b-2507-voicevox.yaml` の
   `profile: local-qwen-voicevox-sim` を `profile: qwen-text` + `simulation: true` に更新。
4. `scripts/bench_llm.py` に bench YAML `simulation:` → `kwargs["g"]["simulation"]` の注入を追加。
5. `docs/env-vs-config.md` に blueprint 軸と run-mode 軸を追記（責務モデルの表）。
6. 旧 profile 名を参照する docs（superpowers/specs・plans 等）は履歴として残し、新規参照のみ新名に。
   ※ `tests/robot/cli/test_profile_resolution.py` は tmp に自前 fixture を作るため実 profile 名に非依存（影響なし）。

## テスト

- `tests/robot/cli/test_profile_resolution.py`: 既存のまま green（実 profile 名非依存）。
- 新規: `TimedMcpClient` の `model` Field default が `DIMOS_LLM_MODEL` を seed し、
  config 明示値が勝つことのユニットテスト（env-vs-config の precedence パターン）。
- 新規: blueprint import 時に `DIMOS_LLM_MODEL` env を読まない（model 焼き込みが無い）ことの確認。
- bench スモーク: 新 `qwen-text` profile + `simulation: true` の bench YAML で
  `kwargs["g"]["simulation"]` が true になることの確認。
- 手動: §3 の起動マトリクス 5 行が起動すること（sim/real × blueprint）。

## 既知の seam（スコープ外・注記のみ）

- `dimos.py:294` の `g` マージ不全（CLI `g` フラグが config.json の `g` を merge せず置換）。
  upstream ファイル編集になるため本設計では扱わない。本設計で profile から `g` を除去するため実害は回避される。
