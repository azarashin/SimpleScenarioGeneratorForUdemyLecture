# 最小パイプライン実行ガイド

## 概要

このプロジェクトには、ステップ実行エンジン上に構築した最小構成のシナリオ生成パイプラインがあります。

現在実装されているステップは次のとおりです。

- `step-01-generate-character-profiles`: 登場人物プロフィール生成
- `step-02-generate-outline`: シナリオアウトライン生成
- `step-02-review-outline`: 企画整合性を評価し、具体的なアウトラインへ修正（任意）
- `step-03-generate-character-images`: キャラクター基本画像・表情差分生成
- `step-04-generate-sections`: 章・節本文生成
- `step-04-review-sections`: 節全体の自然さ・連続性を評価して修正（任意）
- `step-05-generate-dialogue-tags`: セリフ単位の話者・表情タグ生成
- `step-06-render-html`: 目次・章・節HTMLと話者画像表示の生成

Step 06を単独で実行する場合、メモリ上にないStep 01〜05の生成結果は
`artifacts/step-NN-*.json` から自動的に読み込まれます。画像マニフェストのパスは
runディレクトリを基準に解決され、HTML生成前に画像ファイルの存在が確認されます。
HTMLは `output/<run-id>/index.html` と `chapter-N/` 以下へUTF-8で原子的に
書き込まれ、runディレクトリ外を指すパスは拒否されます。
書き込み後は全ページのローカルリンクと画像パスをページ位置から解決し、リンク切れ、
画像欠損、runディレクトリ外参照が1件でもあればStep 06は失敗します。

シナリオ本文の生成契約は `SCENARIO_BODY_SPEC.md`、会話量を増やすための考え方と調整方法は
`SCENARIO_GENERATION_KNOWHOW.md` を参照してください。
画像生成の設定、実行例、成果物、再開方法は `IMAGE_GENERATION.md` を参照してください。

## 自由形式の企画入力とStep 00

`--input`で指定したファイルが正式な入力JSONでない場合、その全文を自由形式の企画メモとして
読み込みます。`planning_input_generation.enabled=true`のとき、OpenAI Structured Outputsと
`schemas/ai-pipeline-input.schema.json`を使って`scenario_idea`と詳細な
`character_overviews`を生成します。人物ごとに外見、背景、性格、価値観、長所・短所、成長軸、
話し方、人物関係、感情範囲、表情ルールまで生成し、Step 01へ引き継ぎます。

成果物は`artifacts/step-00-generate-planning-input.json`へ保存され、`require_review=true`なら
レビュー待ちで停止します。内容を確認・修正後、同じ入力ファイルとrun IDを指定し、
`--from-step step-01-generate-character-profiles`で明示的に承認・再開します。既存の正式な
`input.json`を指定した場合、Step 00はパイプラインへ挿入されません。

## キャラクター入力とStep 01

`examples/input.json`の`character_overviews`には、最低限のID・名前・役割・概要に加え、
年齢、立場、外見、服装、人物像、背景、成長軸、口調、セリフ例、人物間関係、表情ルールを
指定できます。Step 01はこれらを`character_profiles`へ構造を保って変換します。

画像生成は外見・服装・基本ポーズ・画像用補足を参照し、本文生成は性格、価値観、長所短所、
背景、成長軸、口調、禁止表現、セリフ例、人物間関係を参照します。`emotion_range`は全16表情の
画像生成範囲を削らず、本文中で優先的に使用する表情の範囲として扱います。

`character_profile_generation.enabled=true`の場合、Step 01はOpenAI Structured Outputsと
`schemas/ai-character-profiles.schema.json`を使って詳細プロフィールを生成します。
`require_review=true`なら、成果物の保存後に正常なレビュー待ち状態として停止します。

```text
output/<run-id>/artifacts/step-01-generate-character-profiles.json
```

確認・修正後、`--from-step step-02-generate-outline`で再開します。再開時は編集済み成果物を
専用スキーマとパイプライン整合性規則で再検証してからアウトライン生成へ進みます。

## Step 02の登場人物計画

Step 02は全キャラクターを全節へ一律に割り当てません。主人公と主要な相棒を導入の中心に置き、
師匠、捜査役、ライバル、友人、敵対者などを役割に応じて段階的に初登場させます。
`第5話`、`第1〜4章`、`chapter 3`のような範囲が人物の役割に書かれている場合、その章以外へは
登場させません。1節の参加人数は序盤から終盤へ2人、3人、4人、5人を目安に増やします。

Step 04の本文プロンプトへ渡す人物プロフィールも、その節の`participating_characters`だけに
限定します。まだ登場していない人物の設定が本文へ混入することを防ぎます。

## 画像生成設定

画像生成は `ImageGenerationProvider` を介して実行します。`mock` は外部APIを呼び出さず、
ローカル実行とテスト用の決定的なPNG画像を返します。`openai` はOpenAI Image APIへ接続します。

```json
{
  "image_generation": {
    "provider": "mock",
    "model": "chat-gpt-image-2",
    "width": 1024,
    "height": 1024,
    "expression_sheet_width": 2048,
    "expression_sheet_height": 2048,
    "style_preset": "anime",
    "quality": "high",
    "output_format": "png",
    "timeout_seconds": 300,
    "api_key_env": "OPENAI_API_KEY"
  }
}
```

`width` と `height` は基準画像、`expression_sheet_width` と
`expression_sheet_height` は4×4表情シートの寸法です。表情画像はシートを16等分した寸法になります。
既定値では基準画像が1024×1024、表情シートが2048×2048、各表情が512×512です。
画像APIはキャラクターごとに基準画像と表情シートの計2回呼び出され、16枚の表情画像はローカルで切り出します。

実画像の生成には `provider` を `openai` に変更し、`api_key_env` で指定した環境変数へAPIキーを
設定します。基本画像はOpenAI Image APIの生成エンドポイント、表情差分は基本画像を参照する編集
エンドポイントを使用します。設定名 `chat-gpt-image-2` はAPI呼び出し時に公式モデルID
`gpt-image-2` へ変換されます。

```powershell
$env:OPENAI_API_KEY = "your-api-key"
```

画像成果物の保存前には、キャラクターIDと表情の網羅性、相対パスの安全性、ファイルの存在、
PNG・JPEG・WebP形式、拡張子、画像寸法を検証します。不完全または破損した画像がある場合、
`step-03-generate-character-images.json` は保存されません。

各画像の生成完了時に `artifacts/images/<character-id>/<image-name>.json` へチェックポイントを
保存します。再試行・再開時は、生成条件ハッシュと画像SHA-256が一致する画像を再利用し、欠落、
改変、設定変更のある画像だけを再生成します。`--force` 指定時は全画像を再生成します。

## テキスト生成設定

テキスト生成は `TextGenerationProvider` を経由します。ローカル実行とテストでは決定的なモック実装を利用できます。

接続設定は `text_generation` に記述します。APIキーそのものを設定ファイルへ保存せず、
APIキーを格納する環境変数名だけを指定してください。

```json
{
  "text_generation": {
    "provider": "mock",
    "model": "gpt-4.1-mini",
    "timeout_seconds": 60,
    "api_key_env": "TEXT_GENERATION_API_KEY"
  }
}
```

PowerShellで環境変数を設定する例です。

```powershell
$env:TEXT_GENERATION_API_KEY = "your-api-key"
```

OpenAI Responses APIを使用する場合は、`requirements.txt` の依存関係をインストールし、
`text_generation.provider` を `openai` に変更します。OpenAI用の設定例は
`examples/pipeline.openai.config.json` にあります。

## シナリオ本文生成

セクション生成プロンプト`v2`は、アウトラインの各セクションに対して個別にレンダリングされます。
プロンプトには次の情報が含まれます。

- シナリオアイデア
- 登場人物プロフィール
- 対象の章とセクション
- 前セクションから引き継いだ状態
- 発言可能なキャラクターID
- 出力JSON Schema
- ナレーション、セリフ数、文字数などの品質条件

各セクションはアウトライン内のサブセクション順に生成されます。サブセクション単位で
スキーマ・整合性・品質検証とチェックポイント保存を行い、最後に1セクションへ結合します。

```json
{
  "scenario_body_generation": {
    "subsections_per_section": 3,
    "target_characters": 1200,
    "min_characters": 850,
    "max_characters": 1600,
    "min_dialogue_blocks": 6,
    "max_dialogue_blocks": 14,
    "require_event_mentions": true
  }
}
```

文字数とセリフ数はサブセクション単位の設定です。文字数は空白を除いて計測し、
セリフ数は `type=dialogue` のブロック数です。
本文が文字数下限だけを満たさない場合は、生成済みJSONを材料に1回の補完生成を行います。
既存の展開を維持しながら反応・行動・因果・場面遷移を加筆し、補完後に同じ品質検証を
再実行します。補完で解決しない場合は通常の再試行戦略へ進みます。

アウトラインは `story_plan` にプロットスレッド、伏線、人物アークを保持し、各
`subsection.planned_state_updates` に本文で実現すべき状態差分を定義します。Step 02 Review は
IDの参照と時系列（open/plant/turn より後に resolve/payoff）を検証・修正します。Step 04 は
この予定差分を本文生成の契約として使い、チェックポイントの `state_after.plan_progress` には
実行済みイベント、未解決スレッド、提示済み伏線、人物変化だけを圧縮して引き継ぎます。
Step 02 Reviewは巨大なアウトラインを一括再出力せず、全体台帳を先に生成してから章単位で
具体化します。各章の番号・節数・サブセクション数・イベントIDは入力構造から復元されます。

各サブセクションは本文とともに `state_updates` を返します。場所、所持品、判明事項、
関係変化、新しく登場した人物・場所・組織・物・概念、未解決／解決済みの伏線、次の生成に
必要な短い要約を累積状態へ反映します。次のプロンプトには全本文ではなく、この累積状態と
直近状況の要約を渡すため、過去の設定を維持しながらコンテキスト増大を抑えます。

アウトラインはサブセクション数と同数の固有イベントを持ち、イベントの循環再利用を禁止します。
イベントは内部管理用の`event_id`と物語内容を表す`description`に分離します。本文では
`description`の意味を自然に描写し、`event_id`は`state_updates.completed_event_ids`だけに記録します。
内部IDがナレーションやセリフへ露出した場合は検証エラーになります。
各サブセクションには`start_state`、`state_change`、`end_state`、`must_not_repeat`があり、直前の
完了状態から新しい変化を起こすことを要求します。累積状態v3は`current_subsection`も保持します。
完了済みイベントを再び対象にした場合や、直前とほぼ同じ`continuity_summary`を返した場合は
進行不足として再生成します。

設定値の詳しい考え方は `SCENARIO_GENERATION_KNOWHOW.md` を参照してください。

OpenAIプロバイダーはJSON Schemaに従った出力を要求します。返却内容は単一のJSONオブジェクトである必要があります。
Markdownコードフェンス、説明文、JSON後方の余分な文章、ルート配列、重複キーは形式エラーとして拒否されます。

## 実行方法

画像生成を含む全ステップを実行します。

モック設定で新規実行する例です。

```powershell
python run_pipeline.py --config examples/pipeline.config.json --input examples/input.json
```

OpenAIプロバイダーで実行する例です。

```powershell
python run_pipeline.py `
  --config examples/pipeline.openai.config.json `
  --input examples/input.json `
  --run-id openai-scenario-001
```

OpenAI設定ではテキスト生成と画像生成の両方に `OPENAI_API_KEY` を使用します。画像生成だけを
再開する場合の詳細は `IMAGE_GENERATION.md` を参照してください。

## 既存実行の再開

```powershell
python run_pipeline.py --run-id run-20260718-101010
```

## 指定ステップからの再開

```powershell
python run_pipeline.py `
  --config examples/pipeline.openai.config.json `
  --run-id openai-scenario-001 `
  --from-step step-04-generate-sections
```

## 強制再生成

```powershell
python run_pipeline.py `
  --config examples/pipeline.openai.config.json `
  --run-id openai-scenario-001 `
  --from-step step-04-generate-sections `
  --force
```

生成条件を変更するとプロンプトハッシュも変わり、ハッシュが一致しないチェックポイントは再生成されます。

## チェックポイントと状態引き継ぎ

検証済みの各セクションは、次のディレクトリへアトミックに保存されます。

```text
output/<run-id>/artifacts/sections/
```

再試行や再開時には、有効なチェックポイントを読み込み、最初の未生成または無効なセクションから再開します。
統合成果物 `step-04-generate-sections.json` は、全セクションが成功した後にだけ保存されます。
失敗した実行でも、成功済みのセクションチェックポイントは保持されます。

各チェックポイントの `state_after` には次の情報が保存され、次のセクションへ渡されます。

- キャラクターの現在地
- 所持品
- 既知情報
- 人間関係の変化
- 発生済みイベント
- 未解決のプロット
- 直前のセクション全文

再開時には状態もチェックポイントから復元します。保存された状態とセクション本文が一致しない場合、
そのチェックポイントは無効として再生成します。

## 再試行戦略

再試行は、短時間再試行、プロンプト修正再試行、最終フォールバックのフェーズに分かれます。

```json
{
  "retry_strategy": {
    "short_retries": 1,
    "prompt_revision_retries": 2,
    "fallback_enabled": true
  }
}
```

- 接続エラーやタイムアウトは `short_retry` へ進みます。
- JSON形式、スキーマ、整合性、品質のエラーは `prompt_revision` へ進みます。
- プロンプト修正再試行では、前回の具体的な失敗理由をモデルへ渡します。
- 本番のセクション生成では、固定文やプレースホルダーによる代替成果物を保存しません。

## 整合性・品質検証

成果物を保存する前に次を検証します。

- キャラクターID、名前、役割の一貫性
- 正規化後に曖昧になるキャラクター名
- 未定義の参加者や話者
- 重複したブロックID
- 章・節の順序、番号、タイトル
- 本文文字数
- ナレーションとセリフの件数
- 必須イベントの記述

品質検証に失敗したセクションは、次の形式で保存されます。

```text
output/<run-id>/artifacts/sections/chapter-NNN-section-NNN.rejected.json
```

拒否ファイルには、失敗理由、実測した文字数とブロック数、要求値、生成されたセクション全文が含まれます。

最終エラーには、具体的な理由、試行回数、最後の再試行フェーズ、状態ファイル、トレースログのパスが表示されます。

## 温度ポリシー

生成温度は通常低く設定し、明示的に許可した創作ステップだけ高い温度を利用します。

```json
{
  "temperature_policy": {
    "low_temperature": 0.2,
    "diversity_temperature": 0.7,
    "diversity_steps": [
      "step-02-generate-outline",
      "step-04-generate-sections"
    ]
  }
}
```

設定と異なる温度をステップが報告した場合、その結果は拒否されます。
トレースには `temperature` と `temperature_mode` が記録されます。

## プロンプトバージョン

プロンプトは `prompts/catalog.json` で管理します。再現性を保つため、設定ファイルでバージョンを固定できます。
成功した実行のトレースには、プロンプトバージョンとSHA-256ハッシュが記録されます。

```json
{
  "prompt_versions": {
    "step-01-generate-character-profiles": "v1",
    "step-02-generate-outline": "v1",
    "step-03-generate-character-images": "v2",
    "step-04-generate-sections": "v2"
  }
}
```

2つの完了済み実行を比較する場合は次を使用します。

```powershell
python compare_prompt_runs.py output/run-baseline output/run-candidate
```

比較結果には、試行回数、失敗数、実行時間、入出力トークン、成果物変更の差分が表示されます。

## 出力ファイル

成果物は `output/<run-id>/` 以下へ生成されます。

- `artifacts/*.json`: 各ステップの成果物
- `artifacts/sections/*.json`: セクションごとのチェックポイント
- `artifacts/images/**/*.json`: 画像ごとのチェックポイント
- `assets/characters/<character-id>/*`: キャラクター基本画像と表情差分
- `run-state.json`: ステップの実行状態
- `trace.jsonl`: 詳細な実行トレース
- `summary.json`: 完了したパイプラインの統合結果
