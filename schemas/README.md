# JSON Schema一覧

このディレクトリでは、シナリオ生成パイプラインの入出力を検証するJSON Schemaを管理します。

## 成果物スキーマ

- `input.schema.json`: シナリオの入力データ
- `character-profiles.schema.json`: 登場人物プロフィール
- `scenario-outline.schema.json`: シナリオアウトライン
- `scenario-sections.schema.json`: 章・節ごとのシナリオ本文
- `dialogue-expression-tags.schema.json`: セリフ単位の話者・表情タグ
- `character-image-assets.schema.json`: キャラクター画像アセット
- `rendered-html-pages.schema.json`: 章・節HTMLの生成結果
- `dialogue-speaker-image-rendering.schema.json`: セリフと話者画像の表示対応
- `common.schema.json`: 複数スキーマで使用する共通定義

## ステップ入出力スキーマ

- `step-01-generate-character-profiles.schema.json`
- `step-02-generate-outline.schema.json`
- `step-03-generate-character-images.schema.json`
- `step-04-generate-sections.schema.json`
- `step-05-generate-dialogue-tags.schema.json`
- `step-06-render-html.schema.json`

各 `step-*.schema.json` は、そのステップの `input` と `output` に必要なキーと型を定義します。

## 適用方法

1. 各ステップの実行前に `input` を検証します。
2. 各ステップの実行後に `output` を検証します。
3. 検証に失敗した場合、後続ステップは実行しません。

JSON Schemaへの適合だけでは、登場人物や時系列などの意味的な整合性までは保証できません。
そのため、パイプラインではスキーマ検証後に整合性・品質検証も実施します。
