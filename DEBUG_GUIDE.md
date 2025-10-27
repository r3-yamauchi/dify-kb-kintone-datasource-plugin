# リモートデバッグ設定ガイド

## 概要
このドキュメントは、公式ガイド（https://docs.dify.ai/plugin-dev-ja/0411-remote-debug-a-plugin）を参考に、プラグインをリモートデバッグモードで起動する手順をまとめたものです。

## 準備手順
1. Dify ワークスペースで **プラグイン管理** を開き、該当プラグインを選択してリモートデバッグ用サーバーアドレス（`REMOTE_INSTALL_URL`）とデバッグキー（`REMOTE_INSTALL_KEY`）を控えます。  
   参考: https://docs.dify.ai/plugin-dev-ja/0411-remote-debug-a-plugin
2. プロジェクトルートにある `.env.example` を複製し `.env` にリネームします。存在しない場合は新規に `.env` を作成します。  
   参考: https://docs.dify.ai/plugin-dev-ja/0411-remote-debug-a-plugin

## .env の設定例
```
INSTALL_METHOD=remote
REMOTE_INSTALL_URL=debug.dify.ai:5003
REMOTE_INSTALL_KEY=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```
- `REMOTE_INSTALL_URL` と `REMOTE_INSTALL_KEY` には、Dify コンソールで取得した値をそのまま記載してください。  
  参考: https://docs.dify.ai/plugin-dev-ja/0411-remote-debug-a-plugin

## 起動手順
1. `.env` がプラグインのルートディレクトリに保存されていることを確認します。  
2. プロジェクトルートで `python -m main` を実行すると、`INSTALL_METHOD=remote` 設定に従ってプラグインがリモートデバッグモードで起動し、Dify ワークスペースに一時的にインストールされます。  
   参考: https://docs.dify.ai/plugin-dev-ja/0411-remote-debug-a-plugin
3. プラグインは Dify のプラグイン一覧に表示され、チームメンバーも同じリモートデバッグ環境にアクセス可能です。  
   参考: https://docs.dify.ai/plugin-dev-ja/0411-remote-debug-a-plugin

## 注意事項
- `.env` が読み込まれない場合は、カレントディレクトリがプラグインルートかを確認してください。  
- デバッグ完了後は `.env` の値を削除するか、`INSTALL_METHOD` を元に戻して不意の接続を避けることをおすすめします。
