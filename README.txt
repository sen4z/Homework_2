PostgreSQL и журнал событий

Запуск (Python 3.12+):
  python3 -m venv .venv
  source .venv/bin/activate
  python3 -m pip install -r requirements.txt
  python3 run_local.py --pg-bin /usr/lib/postgresql/18/bin/

Контракт:
  POST /increment
  Content-Type: application/json
  {"number": 5}
  успех: 200, {"result":6,"request_id":"..."}
  повтор: 409, error=DUPLICATE_NUMBER
  n+1 уже успешно обработано: 409, error=PREDECESSOR_OF_PROCESSED
  неверный ввод: 400, error=INVALID_INPUT
  проблема базы / транзакции: 503, error=DATABASE_UNAVAILABLE
  веб-сервер не видит приложение: 502, error=UPSTREAM_UNAVAILABLE

Проверка:
  python3 test_integration.py --pg-bin /path/to/postgresql/bin
Тесты создают временную базу, два приложения и два веб-сервера. Проверяются
границы, неверный ввод, гонки, сохранение истории, откат и недоступность.
Фактический результат: test_results.json. Отдельный запускатель тоже проверен:
результат четырёх HTTP-запросов и завершения - launch_results.json.