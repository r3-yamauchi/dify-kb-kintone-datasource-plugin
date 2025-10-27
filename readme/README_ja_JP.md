## dify-kb-kintone-datasource-plugin（日本語）

英語版 README はプロジェクト直下の `README.md` を参照してください。

[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/r3-yamauchi/dify-kb-kintone-datasource-plugin)

### 概要

kintone のアプリからレコードを取得し、Dify のオンラインドキュメント型データソースとして同期するプラグインです。1 レコード = 1 ページとして扱い、`workspace_id` は kintone アプリ ID、`page_id` はレコード番号 (`$id`) を対応させます。`content` には各フィールドの値を平文で整形して格納します。

### 事前準備

1. kintone 側で対象アプリの API トークンを作成し、レコード閲覧（読み取り）権限を付与します。
2. Dify でプラグインをインポートし、provider 設定画面で以下を入力します。
   - `base_url`: 例 `https://example.cybozu.com`
   - `api_token`: 手順 1 で発行したトークン

### データソース設定項目

| パラメータ | 説明 |
| --- | --- |
| `app_id` | 同期対象の kintone アプリ ID。Dify 側では `workspace_id` として扱われます。 |
| `query` | 任意の kintone クエリ文字列。未設定時は作成順で取得します。 |
| `max_records` | 1 回の同期で取り込むレコード数（1〜500、デフォルト 100）。 |
| `title_field` | ページタイトルに使うフィールドコード。未指定の場合はレコード番号を使用します。 |
| `debug_logging` | true にすると同期処理の進行状況を詳細ログとして出力します。 |

### データマッピング

- `workspace_id`: `app_id` の値をそのまま利用。
- `page_id`: レコード番号（`$id`）。
- `content`: `フィールドコード: 値` を改行区切りで列挙。サブテーブルは `列=値` を `;` で連結し、リスト型フィールドは `, ` 区切りで連結します。

### ベストプラクティス

- 取得件数が 500 件を超える場合は `query` の条件や `max_records` を調整して複数回に分けて同期する。
- `title_field` に検索で判別しやすいフィールド（例: 件名、顧客名など）を指定すると、Dify ナレッジベース上での可読性が向上します。
- API トークンには最小限の権限のみを付与し、必要に応じてローテーションしてください。
