#!/usr/bin/env python3
#
# Descr.:
# Inicializa (ou reinicializa) a base de dados a partir do script SQL v3.
# Cria as tabelas, relações, dados de configuração de limites de sensores
# e popula com dados de exemplo para desenvolvimento.
#
# Uso:
# $ python ./create_db.py
#
# Autor:
# José Guerreiro Faísca
#

import sqlite3
import os
import sys

# Caminho para a base de dados e script SQL
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, '../db/monitor.db')
SQL_PATH = os.path.join(BASE_DIR, '../db/5_IMPLEMENTACAO_v3.sql')

def criar_base_de_dados():
    """Executa o script SQL v3 para criar/recriar a estrutura da base de dados."""
    print(f"[INFO] Base de dados: {DB_PATH}")
    print(f"[INFO] Script SQL:    {SQL_PATH}")

    if not os.path.exists(SQL_PATH):
        print(f"[ERRO] Ficheiro SQL não encontrado: {SQL_PATH}")
        sys.exit(1)

    # Ler script SQL
    with open(SQL_PATH, 'r', encoding='utf-8') as f:
        sql_script = f.read()

    # Criar/recriar base de dados
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")

    try:
        conn.executescript(sql_script)
        conn.commit()
        print("[OK] Estrutura da base de dados criada com sucesso.")
    except Exception as e:
        print(f"[ERRO] Falha ao executar script SQL: {e}")
        conn.close()
        sys.exit(1)

    # Inserir dados de exemplo para desenvolvimento
    cursor = conn.cursor()
    try:
        # Clientes de exemplo
        cursor.executemany('''
            INSERT OR IGNORE INTO Cliente (NIF, Nome, Morada, CodigoPostal, Localidade, Area, Zona)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', [
            ('123456789', 'Supermercados FreshCold Lda', 'Rua das Indústrias, 42',
             '2700-123', 'Amadora', 'Alimentar', 'Lisboa'),
            ('987654321', 'Armazéns Norte Frio SA', 'Av. do Parque Industrial, 8',
             '4100-456', 'Porto', 'Logística', 'Norte'),
        ])

        # Equipamentos de exemplo
        cursor.execute('''
            INSERT OR IGNORE INTO Equipamento (ID, NumeroSerie) VALUES (1, 'SN-DEMO-001')
        ''')
        cursor.execute('''
            INSERT OR IGNORE INTO Equipamento (ID, NumeroSerie) VALUES (2, 'SN-DEMO-002')
        ''')

        # Associar equipamentos a clientes (propriedade inicial)
        cursor.execute('''
            INSERT OR IGNORE INTO PropriedadeEquipamento (Equipamento_ID, Cliente_NIF)
            VALUES (1, '123456789')
        ''')
        cursor.execute('''
            INSERT OR IGNORE INTO PropriedadeEquipamento (Equipamento_ID, Cliente_NIF)
            VALUES (2, '987654321')
        ''')

        conn.commit()
        print("[OK] Dados de exemplo inseridos com sucesso.")
        print("     Clientes: 123456789 (FreshCold), 987654321 (Norte Frio)")
        print("     Equipamentos: ID=1 (SN-DEMO-001), ID=2 (SN-DEMO-002)")

    except Exception as e:
        conn.rollback()
        print(f"[AVISO] Dados de exemplo não inseridos (podem já existir): {e}")
    finally:
        conn.close()

    print("\n[INFO] Base de dados pronta. Pode iniciar o servidor:")
    print("       $ python server_main/server_main.py")

if __name__ == "__main__":
    criar_base_de_dados()
