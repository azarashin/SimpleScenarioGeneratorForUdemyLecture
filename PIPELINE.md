# 最小パイプライン実行ガイド

## 概要

このプロジェクトには、ステップ実行エンジン上に構築した最小構成のシナリオ生成パイプラインがあります。

現在実装されているステップは次のとおりです。

- `step-01-generate-character-profiles`: 登場人物プロフィール生成
- `step-02-generate-outline`: シナリオアウトライン生成
- `step-03-generate-character-images`: キャラクター基本画像・表情差分生成
- `step-04-generate-sections`: 章・節本文生成
- `step-05-generate-dialogue-tags`: セリフ単位の話者・表情タグ生成
- `step-06-render-html`: 目次・章・節HTMLと話者画像表示の生成

Step 06を単独で実行する場合、メモリ上にないStep 01〜05の生成結果は
`artifacts/step-NN-*.json` から自動的に読み込まれます。画像マニフェストのパスは
runディレクトリを基準に解決され、HTML生成前に画像ファイルの存在が確認されます。
HTMLは `output/<run-id>/index.html` と `chapter-N/` 以下へUTF-8で原子的に
書き込まれ、runディレクトリ外を指すパスは拒否されます。

シナリオ本文の生成契約は `SCENARIO_BODY_SPEC.md`、会話量を増やすための考え方と調整方法は
`SCENARIO_GENERATION_KNOWHOW.md` を参照してください。
画像生成の設定、実行例、成果物、再開方法は `IMAGE_GENERATION.md` を参照してください。

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

各セクションはアウトライン順に生成され、スキーマ・整合性・品質検証を通過したものだけが保存されます。

```json
{
  "scenario_body_generation": {
    "min_characters": 1000,
    "max_characters": 3200,
    "min_dialogue_blocks": 20,
    "max_dialogue_blocks": 40,
    "require_event_mentions": true
  }
}
```

文字数は空白を除いて計測します。セリフ数は `type=dialogue` のブロック数です。
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
