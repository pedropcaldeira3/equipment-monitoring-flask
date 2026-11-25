"""Regression tests for the equipment API (Flask test client + temporary SQLite database).

Run from the repository root:
    pip install -r requirements-dev.txt
    pytest
"""
import os
import sqlite3
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for sub in ("server_main", "client_temp", "scripts"):
    sys.path.insert(0, os.path.join(ROOT, sub))

os.environ.setdefault("MONITOR_ADMIN_PASSWORD", "test-password")

import server_main as srv  # noqa: E402
import teste_sistema  # noqa: E402
import temp_rand_main  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "monitor.db")
    monkeypatch.setattr(srv, "DATABASE", db_path)
    srv.init_db()

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executemany("INSERT INTO Cliente (NIF, Nome) VALUES (?, ?)",
                     [("111111111", "Cliente A"), ("222222222", "Cliente B")])
    conn.executemany("INSERT INTO Equipamento (ID, NumeroSerie) VALUES (?, ?)",
                     [(1, "SN-A"), (2, "SN-B")])
    conn.executemany("INSERT INTO PropriedadeEquipamento (Equipamento_ID, Cliente_NIF) VALUES (?, ?)",
                     [(1, "111111111"), (2, "222222222")])
    conn.commit()
    conn.close()

    srv.app.config["TESTING"] = True
    with srv.app.test_client() as test_client:
        test_client.db_path = db_path
        yield test_client


def auth():
    return {"Authorization": f"Bearer {srv.gerar_token(srv.ADMIN_USER)}"}


def query(client, sql, params=()):
    conn = sqlite3.connect(client.db_path)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def ownership(client, equip_id):
    return query(client, """
        SELECT Cliente_NIF, DataInicio, DataFim FROM PropriedadeEquipamento
         WHERE Equipamento_ID = ? ORDER BY ID""", (equip_id,))


def limits(client):
    return {row[0]: (row[1], row[2])
            for row in query(client, "SELECT Sensor_tipo, Min_val, Max_val FROM limites_sensores")}


def assert_consistent_history(history):
    open_records = [r for r in history if r[2] is None]
    assert len(open_records) == 1 and history[-1][2] is None
    for current, following in zip(history, history[1:]):
        assert current[2] is not None and current[2] >= current[1], current
        assert following[1] == current[2], (current, following)


# --- PUT with a serial number that belongs to another unit ------------------

def test_put_duplicate_serial_returns_409_without_changes(client):
    equipment_before = query(client, "SELECT * FROM Equipamento ORDER BY ID")
    ownership_before = query(client, "SELECT * FROM PropriedadeEquipamento ORDER BY ID")

    resp = client.put("/api/equipamentos/2", headers=auth(),
                      json={"NumeroSerie": "SN-A", "Cliente_NIF": "111111111"})

    assert resp.status_code == 409
    assert query(client, "SELECT * FROM Equipamento ORDER BY ID") == equipment_before
    assert query(client, "SELECT * FROM PropriedadeEquipamento ORDER BY ID") == ownership_before


def test_put_valid_request_updates_serial_and_owner(client):
    resp = client.put("/api/equipamentos/2", headers=auth(),
                      json={"NumeroSerie": "SN-B2", "Cliente_NIF": "111111111"})

    assert resp.status_code == 200
    assert query(client, "SELECT NumeroSerie FROM Equipamento WHERE ID = 2") == [("SN-B2",)]
    history = ownership(client, 2)
    assert [r[0] for r in history] == ["222222222", "111111111"]
    assert_consistent_history(history)


# --- consecutive PATCH transfers ---------------------------------------------

def test_rapid_transfers_keep_ownership_history_consistent(client):
    owners = ["222222222", "111111111"] * 3
    for nif in owners:
        resp = client.patch("/api/equipamentos/1", headers=auth(), json={"Cliente_NIF": nif})
        assert resp.status_code == 200

    history = ownership(client, 1)
    assert [r[0] for r in history] == ["111111111"] + owners
    assert_consistent_history(history)


def test_transfer_never_ends_before_current_start(client):
    # Open record starting "in the future" (e.g. clock differences): the end must not precede it
    conn = sqlite3.connect(client.db_path)
    conn.execute("UPDATE PropriedadeEquipamento SET DataInicio = '2999-01-01 00:00:00' "
                 "WHERE Equipamento_ID = 1 AND DataFim IS NULL")
    conn.commit()
    conn.close()

    resp = client.patch("/api/equipamentos/1", headers=auth(), json={"Cliente_NIF": "222222222"})

    assert resp.status_code == 200
    assert_consistent_history(ownership(client, 1))


def test_transfer_to_unknown_client_changes_nothing(client):
    before = ownership(client, 1)
    resp = client.patch("/api/equipamentos/1", headers=auth(), json={"Cliente_NIF": "999999999"})
    assert resp.status_code == 404
    assert ownership(client, 1) == before


# --- normal readings vs configured limits ------------------------------------

def test_simulator_ranges_are_within_configured_limits(client):
    lim = limits(client)
    for sensor, (a, b) in {"temp0": (temp_rand_main.x_0, temp_rand_main.y_0),
                           "temp1": (temp_rand_main.x_1, temp_rand_main.y_1),
                           "temp2": (temp_rand_main.x_2, temp_rand_main.y_2)}.items():
        low, high = lim[sensor]
        assert low <= min(a, b) and max(a, b) <= high, sensor


def test_verification_script_samples_match_limits(client):
    lim = limits(client)
    sensors = ("temp0", "temp1", "temp2")
    out_normal = [s for s, v in zip(sensors, teste_sistema.TEMPERATURA_NORMAL)
                  if not lim[s][0] <= v <= lim[s][1]]
    out_anomalous = [s for s, v in zip(sensors, teste_sistema.TEMPERATURA_ANOMALA)
                     if not lim[s][0] <= v <= lim[s][1]]
    assert out_normal == []
    assert out_anomalous == ["temp0"]


def wait_for_alerts(client, expected, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        count = query(client, "SELECT COUNT(*) FROM alertas_sensores")[0][0]
        if count >= expected:
            break
        time.sleep(0.05)
    time.sleep(0.2)  # let any extra (unexpected) alert be written
    return query(client, "SELECT COUNT(*) FROM alertas_sensores")[0][0]


def test_normal_reading_creates_no_alert_and_anomalous_reading_one(client):
    def send(values):
        t0, t1, t2 = values
        return client.post("/temperatura", headers=auth(), json={
            "equipamento_id": 1, "datahora": "2026-01-01 10:00:00",
            "temp0": t0, "temp1": t1, "temp2": t2})

    assert send(teste_sistema.TEMPERATURA_NORMAL).status_code in (200, 201)
    assert wait_for_alerts(client, expected=0, timeout=0.5) == 0

    assert send(teste_sistema.TEMPERATURA_ANOMALA).status_code in (200, 201)
    assert wait_for_alerts(client, expected=1) == 1
    assert query(client, "SELECT Sensor_tipo FROM alertas_sensores") == [("temp0",)]
