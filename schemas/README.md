# JSON Schema一覧

このディレクトリは、シナリオ生成パイプラインの入出力を固定するためのJSON Schemaを管理する。

## 成果物スキーマ
- `input.schema.json`: コンテンツ制作者入力
- `character-profiles.schema.json`: 登場人物プロフィール
- `scenario-outline.schema.json`: シナリオアウトライン
- `scenario-sections.schema.json`: 章節本文
- `dialogue-expression-tags.schema.json`: セリフ単位の話者・表情タグ
- `character-image-assets.schema.json`: キャラクター画像資産
- `rendered-html-pages.schema.json`: 章/節HTMLの生成結果マニフェスト
- `dialogue-speaker-image-rendering.schema.json`: セリフ表示時の話者画像マッピング
- `common.schema.json`: 共通型定義

## ステップ契約スキーマ
- `step-01-generate-character-profiles.schema.json`
- `step-02-generate-outline.schema.json`
- `step-03-generate-character-images.schema.json`
- `step-04-generate-sections.schema.json`
- `step-05-generate-dialogue-tags.schema.json`
- `step-06-render-html.schema.json`

各 `step-*.schema.json` は、`input` と `output` の必須キー・型を固定する。

## 想定運用
1. 各ステップ実行前に `input` をバリデーションする。
2. 各ステップ実行後に `output` をバリデーションする。
3. バリデーション失敗時は、後段ステップを実行しない。
