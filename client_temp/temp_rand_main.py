#
# Descr.:
# Gera marca temporal (timestamp) e valores aleatorios
# de temperatura e envia para o servidor em formato JSON
# com autenticacao JWT.
#
# Uso:
# $ python ./temp_rand_main.py <ID_do_equipamento>
#
# Autores:
# Jose G. Faisca (base)
# Pedro Caldeira | Tiago Sebastiao
#

import os
import sys
import random
import time
import requests
from datetime import datetime

# ---------------------------------------------------------------------------
# Configuracao
# ---------------------------------------------------------------------------

# Intervalos de geracao de temperatura por sensor
x_0 = -10.00; y_0 = -12.00   # temp0: camara de congelacao A
x_1 = -20.00; y_1 = -22.00   # temp1: camara de congelacao B
x_2 =  18.00; y_2 =  20.00   # temp2: ambiente sala tecnica

# Intervalo entre leituras (segundos)
s = 10

# Endereco do servidor
host = "localhost"
port = "8080"
url_temperatura = f"http://{host}:{port}/temperatura"
url_login       = f"http://{host}:{port}/api/auth/login"

# Credenciais para autenticacao JWT (variaveis de ambiente, ver .env.example)
UTILIZADOR = os.environ.get("MONITOR_ADMIN_USER", "admin")
PASSWORD   = os.environ.get("MONITOR_ADMIN_PASSWORD", "")

# ---------------------------------------------------------------------------
# Autenticacao
# ---------------------------------------------------------------------------

def obter_token_jwt():
    """Efetua login na API e devolve o token JWT.
    Termina o programa se nao conseguir autenticar."""
    try:
        resp = requests.post(url_login,
                             json={"utilizador": UTILIZADOR, "password": PASSWORD},
                             timeout=10)
        if resp.status_code == 200:
            token = resp.json().get("token")
            print(f"[AUTH] Token JWT obtido com sucesso.")
            return token
        else:
            print(f"[ERRO] Falha na autenticacao: {resp.status_code} - {resp.text}")
            sys.exit(1)
    except requests.exceptions.ConnectionError:
        print(f"[ERRO] Nao foi possivel ligar ao servidor em {host}:{port}.")
        sys.exit(1)
    except Exception as e:
        print(f"[ERRO] Erro ao obter token: {e}")
        sys.exit(1)

# ---------------------------------------------------------------------------
# Ciclo principal
# ---------------------------------------------------------------------------

def main(equipment_id):
    # Obter token JWT antes de iniciar o ciclo de envio
    token = obter_token_jwt()
    cabecalhos = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    try:
        while True:
            # Obter marca temporal atual
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # Gerar valores de temperatura aleatorios dentro dos intervalos definidos
            temp0 = round(random.uniform(x_0, y_0), 1)
            temp1 = round(random.uniform(x_1, y_1), 1)
            temp2 = round(random.uniform(x_2, y_2), 1)

            # Construir payload JSON conforme exigido pela alinea D do enunciado
            payload = {
                "equipamento_id": equipment_id,
                "datahora": current_time,
                "temp0": temp0,
                "temp1": temp1,
                "temp2": temp2
            }

            print(f"\n[ENVIO] ID={equipment_id} | {current_time} | "
                  f"temp0={temp0} temp1={temp1} temp2={temp2}")

            try:
                # Enviar dados em formato JSON com token JWT no cabecalho
                response = requests.post(url_temperatura,
                                         json=payload,
                                         headers=cabecalhos,
                                         timeout=10)

                print(f"[RESP ] Status: {response.status_code} | {response.text.strip()}")

                # Se o token expirou (401), renovar automaticamente
                if response.status_code == 401:
                    print("[AUTH] Token expirado. A renovar...")
                    token = obter_token_jwt()
                    cabecalhos["Authorization"] = f"Bearer {token}"

            except requests.exceptions.ConnectionError:
                print(f"[AVISO] Servidor inacessivel. Nova tentativa em {s}s...")
            except requests.exceptions.Timeout:
                print(f"[AVISO] Timeout na ligacao. Nova tentativa em {s}s...")

            # Pausa entre leituras
            time.sleep(s)

    except KeyboardInterrupt:
        print("\n[INFO] Processo interrompido pelo utilizador.")
    finally:
        print("[INFO] Terminar script de temperatura.")

# ---------------------------------------------------------------------------
# Ponto de entrada
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"Uso: python {sys.argv[0]} <ID_do_equipamento>")
        sys.exit(1)

    equipment_id = sys.argv[1]
    print(f"[INFO] Iniciando monitorizacao do equipamento ID: {equipment_id}")
    main(equipment_id)
