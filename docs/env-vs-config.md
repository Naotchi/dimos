# env vs config 責務分離

dimos の設定値を「環境変数 (env)」と「設定ファイル (config)」のどちらに置くかを決めるためのルール。

## 3 カテゴリ

| カテゴリ | 例 | 置き場所 | 理由 |
|---|---|---|---|
| **A. 振る舞いの選択** | backend impl, model 名, fp16, speaker_id, 速度 | **config field**（env は default seed のみ） | 再現性が要る。bench/CI で YAML/JSON に残る。 |
| **B. 秘匿情報 / デプロイ依存エンドポイント** | API key, private base URL | **env only** | secret/マシン依存値は config file に書けない。 |
| **C. プロセス境界の env** | `OPENAI_API_KEY`, `OPENAI_BASE_URL` | **env only**（外部 SDK が直接読む） | dimos の管理外。 |

## 優先順位

`explicit config field > env (seed) > Field default`

A のフィールドは `Field(default_factory=lambda: os.environ.get(...))` で env を seed として読み取る。explicit な config 値が常に勝つ。

例:

```python
class VoicevoxParamsConfig(ModuleConfig):
    speaker_id: int = Field(
        default_factory=lambda: int(os.environ.get("DIMOS_VOICEVOX_SPEAKER_ID", "74"))
    )
```

- `VoicevoxParamsConfig()` → env に `DIMOS_VOICEVOX_SPEAKER_ID=99` があれば 99、なければ 74
- `VoicevoxParamsConfig(speaker_id=42)` → env に何が入っていても 42

## Anti-pattern

1. **A の値を env だけでしか変えられない**: bench YAML / profile config.json に書けないため、比較実験が記録に残らない。値を見ても何で動いていたか分からない。
2. **B/C を config file に書く**: 誤って secret を commit するリスク。マシン依存値が他マシンで動かない。
3. **シードを 2 箇所で読む**: Config の Field default と Node の `__init__` の両方で env を読むと、優先順位ルールが破綻する。env 読みは Config 層に集約する。

## profile (`dimos run --profile NAME`) との関係

profile は単一ファイル `configs/profiles/NAME.json`（category A のバンドル）。endpoint の secret は profile に置かず root `.env` に集約する（詳細は下記「profile レイアウト: 単一ファイル + endpoint セレクタ」2026-05-24 追記）。

- `NAME.json` → category A の値（`timedmcpclient.model` / `timedmcpclient.endpoint` 等）
- root `.env` → category B/C の endpoint 資格情報（`DIMOS_LLM_{LOCAL,CLOUD}_*`）。gitignore。

profile は **値の置き場ルールを変えない**。category A を `.env` に書いてもなお動くが、それは anti-pattern (1) なので避ける。

詳細は `docs/superpowers/specs/2026-05-19-profile-and-env-config-policy-design.md` 参照。

## blueprint / run-mode 軸（2026-05 追記）

env vs config（A/B/C）は「値の置き場」を決めるが、実際の責務分離はもう 2 軸を含む。

| 軸 | 所有する concern | 選択方法 |
|---|---|---|
| **blueprint**（コード） | トポロジ: module 構成・transport・remapping・capability の有無（detection wiring 等） | `dimos run <bp>` 位置引数 |
| **profile / config.json**（A） | デプロイ調整値: `timedmcpclient.model`（自由切替）, mic_mode, whisper params, tts impl, memory_limit | `--profile NAME` |
| **root `.env`**（B/C） | secret + endpoint 実体（`DIMOS_LLM_{LOCAL,CLOUD}_*`）。local/cloud 選択は `timedmcpclient.endpoint`（A） | root に集約（gitignore） |
| **run-mode（`g.*`）** | `simulation` 等の invocation パラメータ。profile でも blueprint でもない | `dimos run --simulation` / bench YAML `simulation:` |

### 重要原則: 衝突は precedence で裁くのではなく設計で消す

`model` は capability(blueprint) と backend(profile) に跨る共有値だが、**書き手を profile config.json
に一本化**することで衝突源を消す。blueprint は model を焼き込まない（`TimedMcpClientConfig.model` が
`DIMOS_LLM_MODEL` を seed default に持つ category-A field）。precedence
（`explicit > env seed > default`）は衝突を裁くルールではなく、衝突を消した後のフォールバック順。

> detection blueprint には VL モデルの profile（`qwen-vl` / `gpt4o`）を当てる、は enforce しない運用規約。

詳細は `docs/superpowers/specs/2026-05-23-blueprint-profile-env-responsibility-design.md`。

## profile レイアウト: 単一ファイル + endpoint セレクタ（2026-05-24 追記）

profile はディレクトリ（`<name>/config.json` + `<name>/.env`）ではなく
**単一の `configs/profiles/<name>.json`** にまとめた。秘密（endpoint の
URL/鍵）は profile に置かず、**root `.env`** に名前付きで1回ずつ定義する:

| 値 | 軸 | 置き場所 |
|---|---|---|
| endpoint の実体（local/cloud の URL+key） | machine | root `.env`（`DIMOS_LLM_{LOCAL,CLOUD}_{BASE_URL,API_KEY}`） |
| local / cloud のどちらを使うか | profile（category A） | `<name>.json` の `timedmcpclient.endpoint` |
| model 名 | profile（category A） | `<name>.json` の `timedmcpclient.model` |

解決の流れ:

```
<name>.json の timedmcpclient.endpoint = "local"|"cloud"
  → apply_profile が root .env の DIMOS_LLM_<ENDPOINT>_* を
    DIMOS_LLM_BASE_URL / DIMOS_LLM_API_KEY にコピー
  → mirror_llm_endpoint_env() が DIMOS_LLM_* → OPENAI_* にミラー（既存）
```

`endpoint` を top-level でなく `timedmcpclient` の中に置くのは、
集約 config が `extra="forbid"`（`blueprints.py`）で module 名以外の
top-level キーを弾くため。`endpoint` は `TimedMcpClientConfig` の正式
フィールド。`apply_profile` は `local`/`cloud` 以外の値を `ValueError`
で弾く。

これにより machine 変更は root `.env` 1箇所、profile は commit 可能な
JSON 1枚で自己完結し、同じ model を local/cloud で切り替えられる
（spark + cloud / desktop + local も表現可能）。

### root `.env` テンプレート

`.env.example` は harness の `.env*` 書込み拒否で commit できないため、
キー定義はここに置く。root `.env`（gitignored・machine ごとに記入）に
次を定義する:

```
# Local OpenAI-compatible server (LM Studio / vLLM / Ollama)
DIMOS_LLM_LOCAL_BASE_URL=http://localhost:1234/v1
DIMOS_LLM_LOCAL_API_KEY=dummy

# Cloud endpoint (Azure OpenAI v1 or OpenAI cloud)
DIMOS_LLM_CLOUD_BASE_URL=https://<resource>.openai.azure.com/openai/v1
DIMOS_LLM_CLOUD_API_KEY=<azure-or-openai-key>
```

model 名は profile の `timedmcpclient.model` が持つ（ここには書かない）。
