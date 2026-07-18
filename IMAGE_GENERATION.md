# キャラクター画像生成ガイド

## キャラクター画像の解決

`CharacterAssetResolver` は `character_image_assets` と `character_profiles` を
キャラクターIDで索引化し、`resolve(character_id, expression)` で画像を解決します。
指定表情が利用できない場合は基本画像へフォールバックし、どちらも利用できない
場合は `image_path=None` を返します。HTML出力先のディレクトリを `relative_to` に
渡すと、画像パスはそのページから参照できるURLへ変換されます。

```python
resolver = CharacterAssetResolver.from_pipeline_data(
    shared_data,
    run_root=run_root,
    verify_files=True,
)
image = resolver.resolve("c001", "smile", relative_to="chapter-1")
```

## 概要

`step-03-generate-character-images` は、キャラクターごとに次の処理を行います。

1. `neutral` の基準画像を1枚生成する。
2. 基準画像を参照し、固定16表情を4列×4行に配置した表情シートを1枚生成する。
3. 表情シートを固定座標で16等分し、表情ごとの画像ファイルとして保存する。

画像APIの呼び出しはキャラクターごとに2回です。16枚への分割はPillowを使ってローカルで行います。

## 固定表情と配置順

左から右、上から下へ次の順で配置します。

| 行 | 表情 |
| --- | --- |
| 1 | `neutral`, `smile`, `serious`, `thinking` |
| 2 | `surprised`, `worried`, `confused`, `angry` |
| 3 | `sad`, `relieved`, `embarrassed`, `nervous` |
| 4 | `confident`, `doubtful`, `shocked`, `determined` |

既存runのキャラクタープロフィールが旧表情セットの場合は、`step-01-generate-character-profiles` から再実行してください。

## セットアップ

```powershell
python -m pip install -r requirements.txt
```

OpenAIを利用する場合は、設定の `api_key_env` と同名の環境変数を設定します。既定は
`OPENAI_API_KEY` で、テキスト生成と同じキーを利用できます。

```powershell
$env:OPENAI_API_KEY = "your-api-key"
```

## 設定

```json
{
  "image_generation": {
    "provider": "openai",
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

- `width`, `height`: 基準画像の寸法
- `expression_sheet_width`, `expression_sheet_height`: 4×4表情シートの寸法。各辺は4で割り切れる必要があります
- 各表情画像の寸法: 表情シートの幅と高さをそれぞれ4で割った値
- `model`: 既定値は `chat-gpt-image-2`。API送信時は正式ID `gpt-image-2` に変換されます
- `provider`: `mock` または `openai`

既定値では、基準画像は1024×1024、表情シートは2048×2048、切り出し画像は512×512です。

## 実行例

モックで全パイプラインを実行します。

```powershell
python run_pipeline.py `
  --config examples/pipeline.config.json `
  --input examples/input.json `
  --run-id mock-image-sheet-001
```

OpenAI APIで実行します。

```powershell
$env:OPENAI_API_KEY = "your-api-key"

python run_pipeline.py `
  --config examples/pipeline.openai.config.json `
  --input examples/input.json `
  --run-id openai-image-sheet-001
```

画像ステップから再開する場合は、同じrun IDを指定します。

```powershell
python run_pipeline.py `
  --config examples/pipeline.openai.config.json `
  --run-id openai-image-sheet-001 `
  --from-step step-03-generate-character-images
```

## 成果物

```text
output/<run-id>/
├─ artifacts/
│  ├─ step-03-generate-character-images.json
│  └─ images/c001/
│     ├─ base.json
│     ├─ expression-sheet.json
│     └─ expressions/
│        ├─ neutral.json
│        └─ ... determined.json
└─ assets/characters/c001/
   ├─ base.png
   ├─ expression-sheet.png
   └─ expressions/
      ├─ neutral.png
      └─ ... determined.png
```

`base_image_path` は基準画像を指し、`expression_images` は切り出した16枚を表情名で参照します。

## チェックポイントと再開

- 基準画像、表情シート、各切り出し画像に個別チェックポイントを保存します。
- 欠損した切り出し画像だけなら、APIを呼ばず表情シートから再作成します。
- 表情シートが欠損・破損した場合は、基準画像を再利用して表情シートだけを再生成します。
- `--force` を指定すると、基準画像と表情シートを再生成し、全切り出し画像を作り直します。
- APIキーや画像バイト列はチェックポイントに保存しません。

検証では、安全な相対パス、画像形式、実寸、ファイル存在、画像内容、全16表情の網羅性を確認します。
