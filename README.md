# Headlines Dashboard（Render版）

Flask + Render で動くヘッドラインダッシュボードです。

## リポジトリ構成

```
.
├── app.py                  ← Flask サーバー（メイン）
├── pipeline.py             ← スクレイパー（ノートブックから変換）← 自分で配置
├── requirements.txt
├── render.yaml             ← Render デプロイ設定
└── README.md
```

-----

## セットアップ手順

### Step 1. pipeline.py を生成する

既存ノートブックをスクリプトに変換します。

```bash
jupyter nbconvert --to script \
  headline_pipeline_refactored.ipynb \
  --output pipeline
```

生成された `pipeline.py` をこのリポジトリのルートに置きます。

-----

### Step 2. GitHub にリポジトリを作成して push

```bash
git init
git add .
git commit -m "initial commit"
git remote add origin https://github.com/<your-name>/headlines-render.git
git push -u origin main
```

-----

### Step 3. Render でデプロイ

1. [render.com](https://render.com) にアクセスして GitHub でログイン
1. **New → Web Service** をクリック
1. 作成したリポジトリを選択
1. 以下を確認・入力：
   
   |項目           |値                                                       |
   |-------------|--------------------------------------------------------|
   |Runtime      |Python                                                  |
   |Build Command|`pip install -r requirements.txt`                       |
   |Start Command|`gunicorn app:app --workers 1 --threads 4 --timeout 120`|
1. **Create Web Service** をクリック → 自動でビルド＆デプロイ開始

-----

### Step 4. アクセス確認

デプロイ完了後、Render が発行する URL にアクセス：

```
https://headlines-dashboard.onrender.com/
```

初回アクセス時はデータ取得（30〜90秒）が走ります。  
取得完了後に自動でページが更新されます。

-----

## パスワードをかけたい場合（任意）

Render ダッシュボードの **Environment → Add Environment Variable** で以下を追加：

```
BASIC_USER = your_username
BASIC_PASS = your_password
```

設定後は再デプロイが走り、次回アクセスからブラウザの認証ダイアログが表示されます。

-----

## 手動更新の方法

ページ右上の **「今すぐ更新」ボタン** を押すだけです。  
バックグラウンドでスクレイピングが走り、完了後にページが自動リロードされます。

-----

## Render 無料プランの注意点

|制限  |内容                                  |対策                                                    |
|----|------------------------------------|------------------------------------------------------|
|スリープ|15分アクセスがないとサーバー停止（次のアクセスで約30秒かけて再起動）|[UptimeRobot](https://uptimerobot.com) で5分おきに ping を送る|
|月間稼働|750時間/月                             |1サービスなら実質無制限                                          |

-----

## ローカルで動作確認する場合

```bash
pip install -r requirements.txt
python app.py
# → http://localhost:5000 にアクセス
```
