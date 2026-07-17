# 要件定義（入力・中間成果物）

## 1. 入力定義

コンテンツ制作者が事前に準備する入力は、以下の2つとする。

### 1-1. シナリオアイデア（scenario_idea）
- 目的: 作品全体の方向性を定義する。
- 形式: JSONオブジェクト
- 必須項目:
  - `title` (string): シナリオの仮タイトル
  - `genre` (string): ジャンル（例: SF, ミステリー, 学園）
  - `theme` (string): 中核テーマ
  - `premise` (string): 物語の前提・導入
  - `target_length` (object): 想定分量
    - `chapter_count` (integer, >=1)
    - `sections_per_chapter` (integer, >=1)
- 任意項目:
  - `tone` (string): 文体や雰囲気（シリアス/コミカル等）
  - `must_include` (string[]): 必須要素
  - `must_avoid` (string[]): 禁止要素
  - `audience` (string): 想定読者層
- 受け入れ条件:
  - `title`, `genre`, `theme`, `premise` が空文字でない。
  - `target_length.chapter_count` と `target_length.sections_per_chapter` は正の整数。

### 1-2. 登場人物概要（character_overviews）
- 目的: キャラクター生成の元情報を定義する。
- 形式: JSON配列
- 最小件数: 1件以上
- 各要素（character_overview）の必須項目:
  - `character_id` (string): 一意ID
  - `name` (string): 表示名
  - `role` (string): 役割（主人公、相棒、敵対者など）
  - `summary` (string): 人物の概要
- 任意項目:
  - `age_range` (string)
  - `gender` (string)
  - `speech_style_hint` (string): 口調ヒント
  - `appearance_hint` (string): 外見ヒント
  - `background_hint` (string): 背景ヒント
  - `relationship_hints` (string[]): 関係性ヒント
- 受け入れ条件:
  - `character_id` の重複がない。
  - `name`, `role`, `summary` が空文字でない。

---

## 2. 中間成果物定義

パイプライン内で生成・保存する中間成果物を以下に定義する。

### 2-1. プロフィール（character_profiles）
- 生成元: `character_overviews`
- 形式: JSON配列
- 各要素の必須項目:
  - `character_id` (string)
  - `name` (string)
  - `role` (string)
  - `personality` (object)
    - `core_traits` (string[])
    - `values` (string[])
    - `weaknesses` (string[])
  - `speech` (object)
    - `style` (string)
    - `first_person` (string)
    - `verbal_tics` (string[])
  - `appearance` (object)
    - `age_impression` (string)
    - `features` (string[])
    - `costume` (string)
  - `emotion_model` (object)
    - `available_expressions` (string[])  # 例: neutral, happy, angry, sad, surprised
- 受け入れ条件:
  - `character_id` が入力の `character_overviews` と一致する。
  - `available_expressions` に `neutral` を含む。

### 2-2. アウトライン（scenario_outline）
- 生成元: `scenario_idea` + `character_profiles`
- 形式: JSONオブジェクト
- 必須項目:
  - `title` (string)
  - `logline` (string)
  - `chapters` (array)
- `chapters[]` の必須項目:
  - `chapter_no` (integer)
  - `chapter_title` (string)
  - `chapter_goal` (string)
  - `sections` (array)
- `sections[]` の必須項目:
  - `section_no` (integer)
  - `section_title` (string)
  - `section_purpose` (string)
  - `key_events` (string[])
  - `participating_characters` (string[])  # character_id
- 受け入れ条件:
  - 章・節番号が昇順で重複なし。
  - `participating_characters` は定義済み `character_id` のみ。

### 2-3. 章節本文（scenario_sections）
- 生成元: `scenario_outline` + `character_profiles`
- 形式: JSON配列
- 各要素の必須項目:
  - `chapter_no` (integer)
  - `section_no` (integer)
  - `section_title` (string)
  - `narrative_blocks` (array)
- `narrative_blocks[]` の必須項目:
  - `block_id` (string)
  - `type` (string: narration | dialogue)
  - `text` (string)
  - `speaker_id` (string|null)  # narration の場合は null
- 受け入れ条件:
  - `type=dialogue` の場合、`speaker_id` は必須。
  - 本文はアウトラインの章節構造と1対1で対応する。

### 2-4. セリフ単位の話者・表情タグ（dialogue_expression_tags）
- 生成元: `scenario_sections` + `character_profiles`
- 形式: JSON配列
- 各要素の必須項目:
  - `chapter_no` (integer)
  - `section_no` (integer)
  - `block_id` (string)
  - `speaker_id` (string)
  - `expression` (string)
  - `emotion_reason` (string)
- 受け入れ条件:
  - `block_id` は `scenario_sections` の dialogue ブロックに存在する。
  - `expression` は該当キャラの `available_expressions` に含まれる。

### 2-5. 画像パス（character_image_assets）
- 生成元: 画像生成ステップ
- 形式: JSON配列
- 各要素の必須項目:
  - `character_id` (string)
  - `base_image_path` (string)
  - `expression_images` (object)
    - key: expression名（string）
    - value: 画像ファイルパス（string）
- 受け入れ条件:
  - `base_image_path` のファイルが存在する。
  - `expression_images` は `available_expressions` を少なくともすべてカバーする。

---

## 3. 出力定義

最終成果物として、章ごと・節ごとに分割されたHTMLと、セリフごとの話者画像表示を提供する。

### 3-1. 章ごと・節ごとのHTML（rendered_html_pages）
- 生成元: `scenario_sections` + `dialogue_expression_tags` + `character_image_assets`
- 出力形式: HTMLファイル群
- 想定ディレクトリ構成:
  - `output/index.html`: 目次ページ
  - `output/chapter-{chapter_no}/index.html`: 章トップページ
  - `output/chapter-{chapter_no}/section-{section_no}.html`: 節ページ
- 各節ページの必須表示要素:
  - 作品タイトル
  - 章番号・章タイトル
  - 節番号・節タイトル
  - 本文ブロック（地の文/セリフ）
  - ナビゲーション（前節・次節・章トップ・目次）
- 受け入れ条件:
  - `scenario_outline` の全章・全節に対応するHTMLが生成される。
  - リンク切れ（目次、章、節、前後遷移）がない。
  - 文字コードは UTF-8 とする。

### 3-2. セリフごとの話者画像表示（dialogue_speaker_image_rendering）
- 生成元: `dialogue_expression_tags` + `character_image_assets`
- 適用対象: 各節ページ内の `type=dialogue` ブロック
- 必須表示仕様:
  - 各セリフに対して話者名を表示する。
  - 各セリフに対して `speaker_id` と `expression` に対応する画像を表示する。
  - 表情画像が存在しない場合は、同一話者の `base_image_path` をフォールバック表示する。
  - 画像には代替テキスト（alt）を設定する。
    - 形式: `{話者名} - {expression}`（フォールバック時は `{話者名} - base`）
- 受け入れ条件:
  - `dialogue_expression_tags` の全 dialogue レコードが表示上で対応付けられる。
  - 各セリフに話者名と画像が1つずつ表示される。
  - 画像ファイル未存在時でもページ生成自体は失敗せず、プレースホルダーまたは代替表示で継続できる。

---

## 最小入力サンプル（JSON）

```json
{
  "scenario_idea": {
    "title": "時空図書館の見習い",
    "genre": "ファンタジー",
    "theme": "記憶と選択",
    "premise": "失われた記憶を本として管理する図書館で、見習い司書が禁書事件に巻き込まれる。",
    "target_length": {
      "chapter_count": 3,
      "sections_per_chapter": 2
    }
  },
  "character_overviews": [
    {
      "character_id": "c001",
      "name": "アオイ",
      "role": "主人公",
      "summary": "新米の見習い司書。観察力が高いが、自信が持てない。"
    }
  ]
}
```
