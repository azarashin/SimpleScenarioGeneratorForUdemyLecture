# キャラクター画像生成ガイド

## 概要

`step-03-generate-character-images` は、`character_profiles` を入力として、キャラクターごとの
基本画像と表情差分を生成します。

- 基本画像: `neutral` 表情のキャラクターデザイン
- 表情差分: 基本画像を参照し、人物・衣装・構図を維持したまま表情を変更
- JSON成果物: `artifacts/step-03-generate-character-images.json`
- 画像ファイル: `assets/characters/<character-id>/`
- 画像チェックポイント: `artifacts/images/<character-id>/`

利用できるプロバイダーは次の2つです。

| provider | 用途 | 外部通信 | 出力 |
| --- | --- | --- | --- |
| `mock` | ローカル実行・テスト | なし | 指定寸法の透明PNG |
| `openai` | 本番画像生成 | OpenAI Image API | PNG・JPEG・WebP |

## セットアップ

依存関係をインストールします。

```powershell
python -m pip install -r requirements.txt
```

OpenAIプロバイダーを使用する場合は、APIキーを環境変数へ設定します。APIキーを設定JSONへ
直接書き込まないでください。

```powershell
$env:OPENAI_API_KEY = "your-api-key"
```

GPT Imageモデルの利用には、OpenAI Organization Verificationが必要になる場合があります。
APIの最新仕様は[OpenAI Image generation guide](https://developers.openai.com/api/docs/guides/image-generation)
を参照してください。

## 設定

### mock設定

`examples/pipeline.config.json` は外部APIを呼び出しません。

```json
{
  "image_generation": {
    "provider": "mock",
    "model": "chat-gpt-image-2",
    "width": 1024,
    "height": 1024,
    "style_preset": "anime",
    "quality": "high",
    "output_format": "png",
    "timeout_seconds": 120,
    "api_key_env": "OPENAI_API_KEY"
  }
}
```

### OpenAI設定

`examples/pipeline.openai.config.json` はテキスト生成と画像生成の両方でOpenAI APIを使用します。

```json
{
  "image_generation": {
    "provider": "openai",
    "model": "chat-gpt-image-2",
    "width": 1024,
    "height": 1024,
    "style_preset": "anime",
    "quality": "high",
    "output_format": "png",
    "timeout_seconds": 120,
    "api_key_env": "OPENAI_API_KEY"
  }
}
```

設定上の `chat-gpt-image-2` は、API呼び出し時に公式モデルID `gpt-image-2` へ変換されます。

| 項目 | 説明 |
| --- | --- |
| `provider` | `mock` または `openai` |
| `model` | 画像生成モデル。既定値は `chat-gpt-image-2` |
| `width`, `height` | 出力画像の実寸 |
| `style_preset` | プロンプトへ含める画風指定 |
| `quality` | `low`、`medium`、`high`、`auto` |
| `output_format` | `png`、`jpeg`、`webp` |
| `timeout_seconds` | 1回のAPI呼び出しのタイムアウト秒数 |
| `api_key_env` | APIキーを格納する環境変数名 |

`gpt-image-2` の画像サイズには次の制約があります。

- 各辺は16ピクセルの倍数
- 最大辺は3840ピクセル以下
- 長辺と短辺の比率は3:1以下
- 総ピクセル数は655,360以上8,294,400以下

## 実行例

### mockで全パイプラインを実行

```powershell
python run_pipeline.py `
  --config examples/pipeline.config.json `
  --input examples/input.json `
  --run-id mock-image-scenario-001
```

### OpenAI APIで全パイプラインを実行

```powershell
$env:OPENAI_API_KEY = "your-api-key"

python run_pipeline.py `
  --config examples/pipeline.openai.config.json `
  --input examples/input.json `
  --run-id openai-image-scenario-001
```

### 画像生成ステップから再開

step-01とstep-02の完了成果物が存在するrunを指定します。画像生成後、step-04も続けて実行されます。

```powershell
python run_pipeline.py `
  --config examples/pipeline.openai.config.json `
  --run-id openai-scenario-001 `
  --from-step step-03-generate-character-images
```

### 欠落・破損画像だけ再生成

同じrun IDでstep-03から再開します。有効な画像チェックポイントは再利用され、欠落、改変、
生成条件変更のある画像だけが再生成されます。

```powershell
python run_pipeline.py `
  --config examples/pipeline.openai.config.json `
  --run-id openai-scenario-001 `
  --from-step step-03-generate-character-images
```

### 全画像を強制再生成

`--force` はstep-03の画像チェックポイントを無視します。step-04も再実行対象になりますが、
有効なセクションチェックポイントは現在の本文生成処理によって再利用されます。

```powershell
python run_pipeline.py `
  --config examples/pipeline.openai.config.json `
  --run-id openai-scenario-001 `
  --from-step step-03-generate-character-images `
  --force
```

## 生成処理

キャラクターごとに次の順序で処理します。

1. `neutral` の基本画像を生成する。
2. 基本画像を `base_image_path` と `expression_images.neutral` に登録する。
3. `available_expressions` のうち `neutral` 以外を順番に生成する。
4. OpenAIプロバイダーでは基本画像を編集入力として渡す。
5. 画像を検証して保存する。
6. 画像単位のチェックポイントを保存する。
7. 全画像成功後に統合JSON成果物を保存する。

## 出力構造

```text
output/<run-id>/
├─ artifacts/
│  ├─ step-03-generate-character-images.json
│  └─ images/
│     └─ c001/
│        ├─ base.json
│        ├─ happy.json
│        └─ sad.json
├─ assets/
│  └─ characters/
│     └─ c001/
│        ├─ base.png
│        ├─ happy.png
│        └─ sad.png
├─ run-state.json
└─ trace.jsonl
```

統合成果物の例です。

```json
{
  "character_image_assets": [
    {
      "character_id": "c001",
      "base_image_path": "assets/characters/c001/base.png",
      "expression_images": {
        "neutral": "assets/characters/c001/base.png",
        "happy": "assets/characters/c001/happy.png",
        "sad": "assets/characters/c001/sad.png"
      }
    }
  ]
}
```

画像チェックポイントには次の情報が保存されます。

- 生成条件ハッシュ
- 画像SHA-256
- runディレクトリからの相対パス
- MIMEタイプ
- APIが使用したモデルID

APIキーや画像バイト列はチェックポイントへ保存しません。

## 検証

統合成果物を保存する前に次を確認します。

- キャラクタープロフィールと画像成果物のIDが一致する
- `available_expressions` の全表情が存在する
- 未定義の表情が含まれていない
- 画像パスがrunディレクトリ内の安全な相対パスである
- 画像ファイルが存在し、空ではない
- PNG、JPEG、WebPとして読み取れる
- 拡張子、画像内容、設定寸法が一致する
- PNGのチャンクCRCと圧縮データが正常である

## トレースイベント

`trace.jsonl` には画像単位で次のイベントが記録されます。

- `image_generated`: APIまたはmockで画像を生成した
- `image_checkpoint_loaded`: 検証済みチェックポイントを再利用した
- `step_succeeded`: 全画像の生成と検証が完了した
- `step_failed`: 画像生成または検証に失敗した

## トラブルシューティング

### APIキーが設定されていない

`api_key_env` と同名の環境変数を設定します。

```powershell
$env:OPENAI_API_KEY = "your-api-key"
```

### Organization Verificationを要求された

OpenAIの組織設定でVerificationを完了してください。GPT ImageモデルではVerificationが必要になる
場合があります。

### 画像サイズが拒否された

幅と高さを16の倍数にし、`gpt-image-2` の辺長、縦横比、総ピクセル数制約を満たしてください。
まずは `1024x1024` を推奨します。

### moderationで拒否された

自動再試行だけでは解決しません。キャラクター設定や画像プロンプトから、安全上問題になる表現を
除いて再実行してください。APIキー、プロンプト全文、個人情報を公開ログへ書き出さないでください。

### 途中でAPIエラーになった

同じrun IDを指定してstep-03から再開してください。成功済み画像はチェックポイントから再利用されます。

### 画像を手動編集した

画像SHA-256がチェックポイントと一致しなくなるため、次回のstep-03実行でその画像が再生成されます。
手動編集を正式な成果物として残す場合は、現在の自動生成runとは別に管理してください。
