Вариант 3: PostgreSQL и журнал событий

Проверки и INSERT events выполняются в SERIALIZABLE. Общей строки блокировки
нет. PostgreSQL обнаруживает конфликты; приложение повторяет всю транзакцию
до 12 раз. Повторяются только 40001, 40P01 и 23505 для one_success_per_number.
Другие ошибки возвращают 503. request_history и processed_numbers - SQL-виды
одного журнала events, а не отдельные копии данных.

Структура: client.py -> webserver.py -> application.py -> база данных.
В варианте SQLite доступ к базе вынесен в repository.py.
У каждого варианта собственный каталог .runtime: не смешивать базы вариантов.

Запуск (Python 3.12+):
  python3 -m venv .venv
  source .venv/bin/activate
  python3 -m pip install -r requirements.txt
  python3 run_local.py --pg-bin /path/to/postgresql/bin

Ожидаемые HTTP-коды: 200, 409, 409, 200; успешные результаты 6 и 7.
Ctrl-C завершает дочерние процессы. Данные сохраняются в .runtime.
Если запускать варианты одновременно, задать разные --web-port и --app-port,
а для PostgreSQL ещё --db-port. По умолчанию порты 8080, 8081, 54329.

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
Вход - целое число 0..N, N по умолчанию 1000000; N+1 допустим в ответе.
История содержит корректные числа с бизнес-отказом. Повтор отказа - повтор.
Неверный JSON/тип/диапазон не попадает в историю чисел. В базе сохраняется
вход n, а не вычисленный ответ n+1. Проверка повтора выполняется первой.

Проверка:
  python3 test_integration.py --pg-bin /path/to/postgresql/bin
Тесты создают временную базу, два приложения и два веб-сервера. Проверяются
границы, неверный ввод, гонки, сохранение истории, откат и недоступность.
Фактический результат: test_results.json. Отдельный запускатель тоже проверен:
результат четырёх HTTP-запросов и завершения - launch_results.json.

Диаграммы: components.puml и sequence.puml, готовые PNG/SVG.
Отчёт: report.pdf. Это локальная учебная реализация, без авторизации и TLS.
Условие: Achievement #2, слайд 17 второй лекции. Код и схемы разработаны
для этих вариантов. HTTP-формат и трактовка истории - выбранные уточнения.
