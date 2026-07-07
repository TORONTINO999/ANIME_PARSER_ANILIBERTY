name: Count Releases

on:
  workflow_dispatch: # Только ручной запуск через кнопку в Actions
  push:
    paths:
      - 'count_releases.py' # Автозапуск только при изменении самого скрипта счетчика

env:
  ACTIONS_ALLOW_USE_UNSECURE_NODE_VERSION: true

jobs:
  count-releases:
    runs-on: ubuntu-latest
    permissions:
      contents: read # Запрет на запись/коммиты в репозиторий

    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install requests
        run: pip install requests

      - name: Count Releases
        run: python count_releases.py
