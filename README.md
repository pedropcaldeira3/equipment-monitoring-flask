# Refrigeration equipment monitoring — Flask REST API

Web application for managing clients and refrigeration equipment, receiving temperature readings from **simulated** sensors and logging alerts when readings fall outside configured limits. It includes a Flask REST API with JWT authentication, a SQLite database with equipment ownership history, server-rendered pages and a JavaScript front end, and Swagger documentation.

Academic pair project for the *Web Programming* course (CTeSP in Web and Mobile Development, IPLUSO — Instituto Politécnico da Lusofonia, Lisbon).

## Authors and origin

- **Base project:** José Guerreiro Faísca (lecturer), who provided the starting code for the course. Files that keep his name in their header (`scripts/create_db.py`, `scripts/teste_sistema.py`) and the "(base)" credit in other files are preserved.
- **Project work:** Pedro Caldeira and Tiago Sebastião, who extended the base into the system described below. The work was shared between the two students.

No licence is included: the base code belongs to its author and is published here only as part of this academic project. If you are the author of any part of this code and want it changed or removed, please open an issue.

![Home page](docs/home.png)

## Features

- **Clients and equipment:** create and list clients (identified by NIF) and equipment (serial number), through HTML forms and the JSON API.
- **Equipment life cycle:** `GET`, `PUT`, `PATCH` and `DELETE` on `/api/equipamentos/<id>`. `PATCH` transfers a unit to another client by closing the current ownership record and opening a new one, so the ownership history is kept. Deleting a unit removes its dependent records through `ON DELETE CASCADE`.
- **Temperature readings:** `POST /temperatura` accepts JSON readings with three sensor values (`temp0`, `temp1`, `temp2`); the CSV format of the original base is still accepted.
- **Alerts:** acceptable ranges per sensor are stored in the `limites_sensores` table. After answering the request, the server checks each reading in a background thread and writes out-of-range values to `alertas_sensores`.
- **Authentication:** `POST /api/auth/login` returns a JWT (valid for 24 h); write operations and the alerts endpoints require `Authorization: Bearer <token>`.
- **API documentation:** Swagger UI generated with Flasgger at `/api/docs`.
- **Front end:** Jinja2 templates plus a static page that calls the API with `fetch` and updates the DOM (delete and transfer without reloading). CSS includes a single-column layout for screens up to 640 px.
- **Sensor simulator:** `client_temp/temp_rand_main.py` logs in, then sends random readings for one unit every 10 seconds.
- **Verification script:** `scripts/teste_sistema.py` creates sample data through the API, sends normal and anomalous readings, lists the resulting alerts and checks some HTTP status codes.

| Equipment list | Temperature readings |
|---|---|
| ![Equipment list](docs/equipment-list.png) | ![Temperatures](docs/temperatures.png) |

![Swagger UI](docs/swagger.png)

*Screenshots taken from this repository's code running locally with the sample data.*

## Project structure

```
server_main/        Flask server (server_main.py), templates and static files
client_temp/        temperature sensor simulator
scripts/            database creation, verification script and its sample data
db/                 SQLite schema (5_IMPLEMENTACAO_v3.sql)
```

## Setup and run

Requires Python 3.10+.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Configuration is read from environment variables (see `.env.example`):

| Variable | Purpose | Default |
|---|---|---|
| `MONITOR_ADMIN_USER` | login user name | `admin` |
| `MONITOR_ADMIN_PASSWORD` | login password | random, printed when the server starts |
| `MONITOR_JWT_SECRET` | key used to sign tokens | random on each start |
| `MONITOR_HOST` / `MONITOR_PORT` | server address | `127.0.0.1` / `8080` |
| `MONITOR_DEBUG` | Flask debug mode (`1` to enable) | off |

```bash
export MONITOR_ADMIN_PASSWORD='choose-a-password'
export MONITOR_JWT_SECRET='a-long-random-string'

python scripts/create_db.py           # creates db/monitor.db with sample data (fictitious companies)
python server_main/server_main.py     # http://127.0.0.1:8080  ·  API docs: /api/docs
```

In other terminals, with the same environment variables:

```bash
python client_temp/temp_rand_main.py 1   # send simulated readings for equipment ID 1
python scripts/teste_sistema.py          # run the verification script
```

### Example API calls

```bash
TOKEN=$(curl -s -X POST http://127.0.0.1:8080/api/auth/login \
  -H 'Content-Type: application/json' \
  -d "{\"utilizador\":\"admin\",\"password\":\"$MONITOR_ADMIN_PASSWORD\"}" | python -c "import sys,json;print(json.load(sys.stdin)['token'])")

# transfer equipment 1 to another client
curl -X PATCH http://127.0.0.1:8080/api/equipamentos/1 \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"Cliente_NIF":"987654321"}'

# list alerts
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8080/api/alertas
```

## Verification

Run locally (Python 3.13, the package versions resolved from `requirements.txt`):

- `scripts/create_db.py` created the database and sample data;
- `scripts/teste_sistema.py` completed: token obtained, `/api/alertas` rejected without a token (401), equipment created, normal and anomalous readings sent, alerts listed, 404 for a missing resource;
- two consecutive ownership transfers with `PATCH` returned 200 and both were kept in `PropriedadeEquipamento`;
- `DELETE` returned 200 with a token and 401 without one; a wrong password returned 401;
- the sensor simulator authenticated and its readings were stored.

There is no automated unit-test suite in this repository; `teste_sistema.py` is a manual verification script that runs against a live server.

## Changes from the submitted version

Made for publication; the application logic is unchanged:

- the login credentials and JWT signing key, previously fixed in the code, are read from environment variables (also in the simulator and verification script), and the login form no longer pre-fills the password;
- the server listens on `127.0.0.1` with debug mode off by default (both configurable);
- student numbers were removed from file headers;
- the generated database, the project report and editor/cache files were not included; `requirements.txt` and `.env.example` were added.

## Known limitations

- Single demo user; no user management.
- The server emits a Socket.IO event when temperatures are listed, but no page subscribes to it, so pages do not update by themselves.
- The simulator's value ranges do not match the default limits for `temp0` and `temp1`, so most simulated readings for those sensors generate alerts.
- Runs on Flask's development server with SQLite; it is a course project, not a production deployment.
