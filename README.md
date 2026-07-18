# Simple Scenario Generator for Udemy Lecture

シナリオの入力情報から、キャラクタープロフィール、アウトライン、キャラクター画像、章・節本文を段階的に生成するパイプラインです。

## 関連ドキュメント

- [PIPELINE.md](PIPELINE.md): パイプライン構成、設定、実行方法
- [IMAGE_GENERATION.md](IMAGE_GENERATION.md): 画像生成の設定、成果物、再開方法
- [SCENARIO_BODY_SPEC.md](SCENARIO_BODY_SPEC.md): シナリオ本文の生成仕様
- [SCENARIO_GENERATION_KNOWHOW.md](SCENARIO_GENERATION_KNOWHOW.md): シナリオ生成・画像生成のノウハウ集
- [requirements.md](requirements.md): 成果物と受け入れ条件

## 基本的な実行例

依存関係をインストールします。

```powershell
python -m pip install -r requirements.txt
```

mockプロバイダーで実行します。

```powershell
python run_pipeline.py `
  --config examples/pipeline.config.json `
  --input examples/input.json `
  --run-id mock-scenario-001
```

OpenAI APIで実行します。

```powershell
$env:OPENAI_API_KEY = "your-api-key"

python run_pipeline.py `
  --config examples/pipeline.openai.config.json `
  --input examples/input.json `
  --run-id openai-scenario-001
```
