#!/usr/bin/env python3
#
# Descr.:
# Script independente de testes automáticos do sistema de monitorização.
# Lê dados de um ficheiro JSON local, cria equipamentos/clientes via API
# e verifica os alertas gerados. Usa autenticação JWT em todas as operações.
#
# Uso:
# $ python ./teste_sistema.py
# $ python ./teste_sistema.py --ficheiro dados_teste.json
#
# Autor:
# José Guerreiro Faísca
#

import requests
import json
import sys
import argparse
import time
import os
from datetime import datetime

# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

HOST = "localhost"
PORT = 8080
BASE_URL = f"http://{HOST}:{PORT}"

FICHEIRO_DADOS = os.path.join(os.path.dirname(__file__), "dados_teste.json")

# Cores ANSI para output no terminal
VERDE = "\033[92m"
VERMELHO = "\033[91m"
AMARELO = "\033[93m"
AZUL = "\033[94m"
RESET = "\033[0m"
NEGRITO = "\033[1m"

# ---------------------------------------------------------------------------
# Utilitários de output
# ---------------------------------------------------------------------------

def ok(msg):
    print(f"  {VERDE}✔{RESET} {msg}")

def erro(msg):
    print(f"  {VERMELHO}✘{RESET} {msg}")

def aviso(msg):
    print(f"  {AMARELO}⚠{RESET} {msg}")

def titulo(msg):
    print(f"\n{NEGRITO}{AZUL}{'='*55}{RESET}")
    print(f"{NEGRITO}{AZUL}  {msg}{RESET}")
    print(f"{NEGRITO}{AZUL}{'='*55}{RESET}")

def secao(msg):
    print(f"\n{NEGRITO}▶ {msg}{RESET}")

# ---------------------------------------------------------------------------
# Autenticação
# ---------------------------------------------------------------------------

def obter_token(utilizador=os.environ.get("MONITOR_ADMIN_USER", "admin"),
                password=os.environ.get("MONITOR_ADMIN_PASSWORD", "")):
    """Autentica na API e retorna o token JWT."""
    secao("Autenticação JWT")
    try:
        resp = requests.post(f"{BASE_URL}/api/auth/login",
                             json={"utilizador": utilizador, "password": password},
                             timeout=10)
        if resp.status_code == 200:
            token = resp.json()["token"]
            ok(f"Token JWT obtido (válido 24h)")
            return token
        else:
            erro(f"Falha na autenticação: {resp.status_code} - {resp.text}")
            sys.exit(1)
    except requests.exceptions.ConnectionError:
        erro(f"Servidor inacessível em {BASE_URL}. Verifique se está em execução.")
        sys.exit(1)

# ---------------------------------------------------------------------------
# Funções de teste
# ---------------------------------------------------------------------------

def testar_criar_cliente(token, dados_cliente):
    """Envia um cliente para a API e verifica a resposta."""
    resp = requests.post(f"{BASE_URL}/api/clientes",
                         json=dados_cliente,
                         headers={"Authorization": f"Bearer {token}",
                                  "Content-Type": "application/json"},
                         timeout=10)
    if resp.status_code in (201, 409):
        if resp.status_code == 201:
            ok(f"Cliente NIF={dados_cliente['NIF']} criado com sucesso")
        else:
            aviso(f"Cliente NIF={dados_cliente['NIF']} já existe (ignorado)")
        return True
    else:
        erro(f"Erro ao criar cliente: {resp.status_code} - {resp.text}")
        return False

def testar_criar_equipamento(token, dados_equip):
    """Regista um equipamento na API e retorna o ID atribuído."""
    resp = requests.post(f"{BASE_URL}/api/equipamentos",
                         json=dados_equip,
                         headers={"Authorization": f"Bearer {token}",
                                  "Content-Type": "application/json"},
                         timeout=10)
    if resp.status_code == 201:
        equip_id = resp.json().get("ID")
        ok(f"Equipamento '{dados_equip['NumeroSerie']}' criado com ID={equip_id}")
        return equip_id
    elif resp.status_code == 409:
        aviso(f"Equipamento '{dados_equip['NumeroSerie']}' já existe")
        return None
    else:
        erro(f"Erro ao criar equipamento: {resp.status_code} - {resp.text}")
        return None

def testar_enviar_temperatura(token, equip_id, temp0, temp1, temp2):
    """Envia uma leitura de temperatura e verifica se foi aceite."""
    datahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    payload = f"{equip_id},{datahora},{temp0},{temp1},{temp2}"

    resp = requests.post(f"{BASE_URL}/temperatura",
                         data=payload,
                         headers={"Authorization": f"Bearer {token}"},
                         timeout=10)
    if resp.status_code == 200:
        ok(f"Temperatura enviada: temp0={temp0}, temp1={temp1}, temp2={temp2}")
        return True
    else:
        erro(f"Erro ao enviar temperatura: {resp.status_code} - {resp.text}")
        return False

def testar_alertas(token):
    """Consulta os alertas e apresenta o resultado."""
    resp = requests.get(f"{BASE_URL}/api/alertas",
                        headers={"Authorization": f"Bearer {token}"},
                        timeout=10)
    if resp.status_code == 200:
        alertas = resp.json()
        ok(f"Alertas consultados com sucesso: {len(alertas)} registo(s)")
        for a in alertas[:5]:  # Mostrar no máximo 5
            print(f"     {VERMELHO}⚡{RESET} [{a['DataHora']}] {a['Sensor_tipo'].upper()} = "
                  f"{a['Leitura']}°C | Equip. ID={a['Equipamento_ID']} | {a['Mensagem']}")
        if len(alertas) > 5:
            aviso(f"... e mais {len(alertas) - 5} alerta(s). Consulte /api/alertas para ver todos.")
        return alertas
    else:
        erro(f"Erro ao consultar alertas: {resp.status_code} - {resp.text}")
        return []

def testar_remover_equipamento(token, equip_id):
    """Remove um equipamento via DELETE e verifica o resultado."""
    resp = requests.delete(f"{BASE_URL}/api/equipamentos/{equip_id}",
                           headers={"Authorization": f"Bearer {token}"},
                           timeout=10)
    if resp.status_code == 200:
        ok(f"Equipamento ID={equip_id} removido com sucesso")
        return True
    else:
        erro(f"Erro ao remover equipamento: {resp.status_code} - {resp.text}")
        return False

def testar_sem_token():
    """Verifica que as rotas protegidas rejeitam pedidos sem token (HTTP 401)."""
    resp = requests.get(f"{BASE_URL}/api/alertas", timeout=10)
    if resp.status_code == 401:
        ok("Rota /api/alertas corretamente protegida (401 sem token)")
        return True
    else:
        erro(f"Rota /api/alertas deveria retornar 401, retornou {resp.status_code}")
        return False

def testar_recurso_inexistente(token):
    """Verifica que recursos inexistentes retornam 404."""
    resp = requests.get(f"{BASE_URL}/api/equipamentos/99999",
                        headers={"Authorization": f"Bearer {token}"},
                        timeout=10)
    if resp.status_code == 404:
        ok("Pedido de recurso inexistente corretamente retorna 404")
        return True
    else:
        erro(f"Deveria retornar 404, retornou {resp.status_code}")
        return False

# ---------------------------------------------------------------------------
# Execução principal
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Script de testes automáticos do Sistema de Monitorização"
    )
    parser.add_argument("--ficheiro", default=FICHEIRO_DADOS,
                        help=f"Caminho para o ficheiro JSON de dados de teste (padrão: {FICHEIRO_DADOS})")
    args = parser.parse_args()

    titulo("Sistema de Monitorização — Testes Automáticos")
    print(f"  Servidor: {BASE_URL}")
    print(f"  Ficheiro: {args.ficheiro}")
    print(f"  Data/Hora: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # Carregar dados de teste do ficheiro JSON
    try:
        with open(args.ficheiro, 'r', encoding='utf-8') as f:
            dados = json.load(f)
        ok(f"Ficheiro '{args.ficheiro}' carregado com sucesso")
    except FileNotFoundError:
        erro(f"Ficheiro '{args.ficheiro}' não encontrado.")
        sys.exit(1)
    except json.JSONDecodeError as e:
        erro(f"Ficheiro JSON malformado: {e}")
        sys.exit(1)

    # --- 1. Autenticação ---
    token = obter_token()

    # --- 2. Validação de segurança ---
    secao("Teste de Segurança (sem token)")
    testar_sem_token()

    # --- 3. Criar clientes ---
    secao("Criação de Clientes")
    for cliente in dados.get("clientes", []):
        testar_criar_cliente(token, cliente)

    # --- 4. Criar equipamentos ---
    secao("Criação de Equipamentos")
    ids_criados = []
    for equip in dados.get("equipamentos", []):
        eid = testar_criar_equipamento(token, equip)
        if eid:
            ids_criados.append(eid)

    # --- 5. Enviar temperaturas normais ---
    secao("Envio de Temperaturas (valores normais)")
    if ids_criados:
        testar_enviar_temperatura(token, ids_criados[0], -11.0, -21.0, 19.0)

    # --- 6. Enviar temperatura anómala para gerar alerta ---
    secao("Envio de Temperatura Anómala (deve gerar alerta)")
    if ids_criados:
        # Temp0 = 50°C está fora do intervalo [-28, -18] → deve gerar alerta
        testar_enviar_temperatura(token, ids_criados[0], 50.0, -21.0, 19.0)
        time.sleep(1)  # Aguardar processamento assíncrono do alerta

    # --- 7. Verificar alertas ---
    secao("Verificação de Alertas")
    testar_alertas(token)

    # --- 8. Testar recurso inexistente ---
    secao("Validação de Códigos de Estado HTTP")
    testar_recurso_inexistente(token)

    # --- 9. Remover equipamento de teste (se criado) ---
    if dados.get("remover_apos_teste") and ids_criados:
        secao("Remoção de Equipamento de Teste")
        testar_remover_equipamento(token, ids_criados[-1])

    titulo("Testes concluídos")
    print()

if __name__ == "__main__":
    main()
