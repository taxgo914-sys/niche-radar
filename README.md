# ニッチレーダー

各国の検索エンジンのサジェストから、少数の人だけが検索している新しいワードを毎朝集め、スマホで見るアプリです。

- 収集: GitHub Actions が毎朝6時(日本時間)に `niche_finder.py` を実行します。PCは不要です。
- 閲覧: GitHub Pages で公開される `docs/` がアプリ本体です。ホーム画面に追加すると、普通のアプリのように開けます。

## セットアップ(スマホだけでできます。10分ほど)

1. github.com でアカウントを作り、右上の「+」→「New repository」で新しいリポジトリを作ります。
   - 名前の例: `niche-radar`
   - Public を推奨します(Actions の実行時間が無制限。Private は月2000分までで、この設定なら収まります)
2. リポジトリ画面の「Add file」→「Upload files」で、このフォルダの中身をすべてアップロードします。
   - `.github/workflows/daily.yml` はフォルダ構造ごと必要です。スマホでフォルダが上げられない場合は「Add file」→「Create new file」で、名前欄に `.github/workflows/daily.yml` と打ち、中身を貼り付けてください。
3. 「Settings」→「Pages」→ Source を「Deploy from a branch」、Branch を `main` / `/docs` にして Save。
4. 「Settings」→「Actions」→「General」→ Workflow permissions を「Read and write permissions」にして Save。
5. 「Actions」タブ →「daily-niche-words」→「Run workflow」で初回の収集を実行します(30分ほど)。
6. スマホで `https://あなたのユーザー名.github.io/niche-radar/` を開き、共有ボタン →「ホーム画面に追加」。

## 追う分野を変える

`config.json` の `seeds` を編集します。アプリの「設定」タブから直接編集画面に飛べます。

| 設定 | 意味 |
|---|---|
| `max_requests_per_run` | 1回の実行で送る最大リクエスト数 |
| `max_requests_per_market` | 1か国あたりの上限 |
| `prefixes_per_seed_per_day` | シードごとに毎日試す展開パターンの数。毎日違う組み合わせを回し、数日で一巡します |
| `keep_days` | アプリで遡れる日数 |
| `translate` | 外国語ワードに日本語訳を付けるか (true/false) |
| `translate_skip_markets` | 翻訳しない国 (初期値は日本) |

## 注意

- 日本語訳は Google 翻訳の無料窓口(非公式)を使って収集時に付けます。訳せなかったワードは、アプリの詳細画面の「Google翻訳で見る」から確認できます。
- サジェストの取得先は各社の非公式な窓口です。仕様変更やブロックで取れなくなることがあります。各サービスの利用規約はご自身で確認してください。
- GitHub のサーバーから取得するため、国別の結果が実際の現地の利用者向けと少し違う場合があります。
- 初日はすべての語が「新語」になります。2日目以降に、本当に新しく現れた語だけが残るようになります。
