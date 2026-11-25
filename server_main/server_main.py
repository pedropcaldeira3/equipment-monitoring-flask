#
# Descr.:
# Servidor HTTP principal - Sistema de Monitorizacao de Equipamentos
# Implementa API REST com autenticacao JWT, gestao do ciclo de vida,
# sistema de alertas assincrono e documentacao Swagger/OpenAPI.
#
# Uso:
# $ python ./server_main.py
#
# Autores:
# Jose G. Faisca (base)
# Pedro Caldeira | Tiago Sebastiao
#

from flask import Flask, request, jsonify, render_template, redirect, url_for, send_from_directory, g
from flask_socketio import SocketIO
from flask_cors import CORS
from flasgger import Swagger
import sqlite3
import jwt
import datetime
import threading
import functools
import os
import secrets

# ---------------------------------------------------------------------------
# Configuracao da aplicacao
# ---------------------------------------------------------------------------

# Caminho para a base de dados SQLite
DATABASE = os.path.join(os.path.dirname(__file__), '../db/monitor.db')

# Configuracao por variaveis de ambiente (ver .env.example).
# Chave secreta para assinar tokens JWT: se nao for definida, e gerada uma chave
# aleatoria em cada arranque (os tokens deixam de ser validos apos reiniciar).
JWT_SECRET    = os.environ.get('MONITOR_JWT_SECRET') or secrets.token_hex(32)
# Credenciais de demonstracao: se a password nao for definida, e gerada uma
# password aleatoria e mostrada na consola no arranque.
ADMIN_USER     = os.environ.get('MONITOR_ADMIN_USER', 'admin')
ADMIN_PASSWORD = os.environ.get('MONITOR_ADMIN_PASSWORD') or secrets.token_urlsafe(12)
JWT_ALGORITHM = 'HS256'

app = Flask(__name__, static_folder='static')
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*")

host = os.environ.get('MONITOR_HOST', '127.0.0.1')
port = int(os.environ.get('MONITOR_PORT', '8080'))
debug = os.environ.get('MONITOR_DEBUG', '0') == '1'

# ---------------------------------------------------------------------------
# Configuracao Swagger / OpenAPI - documentacao interativa em /api/docs
# ---------------------------------------------------------------------------

swagger_config = {
    "headers": [],
    "specs": [{
        "endpoint": "apispec",
        "route": "/apispec.json",
        "rule_filter": lambda rule: True,
        "model_filter": lambda tag: True,
    }],
    "static_url_path": "/flasgger_static",
    "swagger_ui": True,
    "specs_route": "/api/docs"
}

swagger_template = {
    "info": {
        "title": "API Sistema de Monitorizacao de Equipamentos",
        "description": (
            "API REST para gestao de clientes, equipamentos, temperaturas e alertas. "
            "Operacoes de escrita requerem token JWT no cabecalho: Authorization: Bearer <token>. "
            "Obter token em POST /api/auth/login com as credenciais definidas em MONITOR_ADMIN_USER / MONITOR_ADMIN_PASSWORD."
        ),
        "version": "1.0.0"
    },
    "securityDefinitions": {
        "Bearer": {
            "type": "apiKey",
            "name": "Authorization",
            "in": "header",
            "description": "Formato: Bearer <token>"
        }
    }
}

swagger = Swagger(app, config=swagger_config, template=swagger_template)

# ---------------------------------------------------------------------------
# Gestao da ligacao a base de dados
# ---------------------------------------------------------------------------

def get_db():
    """Devolve a ligacao SQLite associada ao contexto do pedido atual.
    Cria a ligacao na primeira chamada e reutiliza-a nas seguintes."""
    if 'db' not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
        # Ativar integridade referencial por sessao (o SQLite exige isto explicitamente)
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db

@app.teardown_appcontext
def close_db(exception=None):
    """Fecha a ligacao a base de dados no fim de cada pedido HTTP."""
    db = g.pop('db', None)
    if db is not None:
        db.close()

def init_db():
    """Cria a estrutura da base de dados a partir do script SQL v3."""
    sql_path = os.path.join(os.path.dirname(__file__), '../db/5_IMPLEMENTACAO_v3.sql')
    conn = sqlite3.connect(DATABASE)
    conn.execute("PRAGMA foreign_keys = ON")
    with open(sql_path, 'r', encoding='utf-8') as f:
        conn.executescript(f.read())
    conn.commit()
    conn.close()
    print("[DB] Base de dados inicializada com sucesso.")

# ---------------------------------------------------------------------------
# Autenticacao JWT
# ---------------------------------------------------------------------------

def gerar_token(utilizador):
    """Gera e devolve um token JWT assinado, com validade de 24 horas."""
    payload = {
        'sub': utilizador,
        'iat': datetime.datetime.utcnow(),
        'exp': datetime.datetime.utcnow() + datetime.timedelta(hours=24)
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

def verificar_token(f):
    """Decorador que protege uma rota, exigindo token JWT valido no cabecalho
    Authorization: Bearer <token>. Devolve 401 se ausente ou invalido."""
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        auth_header = request.headers.get('Authorization', '')
        if not auth_header.startswith('Bearer '):
            return jsonify({'erro': 'Token JWT em falta. Use: Authorization: Bearer <token>'}), 401
        token = auth_header.split(' ', 1)[1]
        try:
            jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        except jwt.ExpiredSignatureError:
            return jsonify({'erro': 'Token JWT expirado. Efetue novo login.'}), 401
        except jwt.InvalidTokenError:
            return jsonify({'erro': 'Token JWT invalido.'}), 401
        return f(*args, **kwargs)
    return wrapper

# ---------------------------------------------------------------------------
# Sistema de alertas assincrono
# ---------------------------------------------------------------------------

def verificar_alertas_async(equipamento_id, temp0, temp1, temp2, datahora):
    """Lanca uma thread separada para validar as temperaturas contra os limites
    configurados na tabela limites_sensores. Se uma leitura estiver fora do
    intervalo razoavel, regista um alerta em alertas_sensores.
    A execucao assincrona garante que o servidor continua a responder a outros
    pedidos enquanto a validacao decorre."""

    def processar():
        # Cada thread precisa da sua propria ligacao ao SQLite
        conn = sqlite3.connect(DATABASE)
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            cursor = conn.cursor()

            # Ler limites configurados para cada sensor (evita valores fixos no codigo)
            cursor.execute("SELECT Sensor_tipo, Min_val, Max_val FROM limites_sensores")
            limites = {row[0]: (row[1], row[2]) for row in cursor.fetchall()}

            leituras = {'temp0': temp0, 'temp1': temp1, 'temp2': temp2}

            for sensor, valor in leituras.items():
                if sensor not in limites:
                    continue
                min_val, max_val = limites[sensor]

                # Verificar se o valor esta fora do intervalo razoavel
                if valor < min_val or valor > max_val:
                    mensagem = (
                        f"ALERTA: {sensor.upper()} = {valor}C fora do intervalo "
                        f"[{min_val}C, {max_val}C] | Equipamento ID={equipamento_id}"
                    )
                    # Usar transacao para garantir persistencia do alerta
                    cursor.execute('''
                        INSERT INTO alertas_sensores
                            (Equipamento_ID, Sensor_tipo, Leitura, DataHora, Mensagem)
                        VALUES (?, ?, ?, ?, ?)
                    ''', (equipamento_id, sensor, valor, datahora, mensagem))
                    print(f"[ALERTA] {mensagem}")

            conn.commit()

        except Exception as e:
            conn.rollback()
            print(f"[ERRO] Processamento de alertas: {e}")
        finally:
            conn.close()

    # Iniciar processamento em thread independente (daemon termina com o servidor)
    t = threading.Thread(target=processar, daemon=True)
    t.start()

# ---------------------------------------------------------------------------
# Autenticacao - rota de login
# ---------------------------------------------------------------------------

@app.route('/api/auth/login', methods=['POST'])
def login():
    """
    Autenticacao: obtencao de token JWT
    ---
    tags:
      - Autenticacao
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [utilizador, password]
          properties:
            utilizador:
              type: string
              example: admin
            password:
              type: string
              example: change-me
    responses:
      200:
        description: Token JWT gerado com sucesso
      400:
        description: Corpo JSON em falta ou incompleto
      401:
        description: Credenciais invalidas
    """
    data = request.get_json()
    if not data or 'utilizador' not in data or 'password' not in data:
        return jsonify({'erro': 'Campos obrigatorios em falta: utilizador, password'}), 400

    utilizador = data.get('utilizador', '')
    password   = data.get('password', '')

    # Credenciais fixas para demonstracao (num sistema real usaria base de dados de utilizadores)
    if secrets.compare_digest(utilizador, ADMIN_USER) and secrets.compare_digest(password, ADMIN_PASSWORD):
        token = gerar_token(utilizador)
        return jsonify({
            'token': token,
            'tipo': 'Bearer',
            'expira_em': '24 horas',
            'mensagem': 'Login efetuado com sucesso'
        }), 200

    return jsonify({'erro': 'Credenciais invalidas'}), 401

# ---------------------------------------------------------------------------
# Interface web - paginas HTML
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    """Pagina principal da aplicacao."""
    return send_from_directory(app.static_folder, 'index.html')

@app.route('/cliente', methods=['GET', 'POST'])
def cliente():
    """Formulario HTML para adicionar um novo cliente."""
    db = get_db()
    cursor = db.cursor()

    if request.method == 'POST':
        nif          = request.form.get('nif', '').strip()
        nome         = request.form.get('nome', '').strip()
        morada       = request.form.get('morada', '').strip()
        codigo_postal = request.form.get('codigo_postal', '').strip()
        localidade   = request.form.get('localidade', '').strip()
        area         = request.form.get('area', '').strip()
        zona         = request.form.get('zona', '').strip()

        if not nif or not nome:
            return render_template('cliente_form.html', erro="NIF e Nome sao obrigatorios.")

        try:
            cursor.execute('''
                INSERT INTO Cliente (NIF, Nome, Morada, CodigoPostal, Localidade, Area, Zona)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (nif, nome, morada, codigo_postal, localidade, area, zona))
            db.commit()
            return redirect(url_for('index'))
        except sqlite3.IntegrityError:
            return render_template('cliente_form.html',
                                   erro=f"Ja existe um cliente com o NIF {nif}.")

    return render_template('cliente_form.html')

@app.route('/equipamento', methods=['GET', 'POST'])
def equipamento():
    """Formulario HTML para adicionar um novo equipamento."""
    db = get_db()
    cursor = db.cursor()

    if request.method == 'POST':
        numero_serie = request.form.get('numero_serie', '').strip()
        cliente_nif  = request.form.get('cliente_nif', '').strip()

        cursor.execute("SELECT NIF, Nome FROM Cliente")
        clientes = cursor.fetchall()

        if not numero_serie or not cliente_nif:
            return render_template('equipamento_form.html', clientes=clientes,
                                   erro="Numero de serie e NIF do cliente sao obrigatorios.")

        # Verificar se o cliente existe antes de inserir
        cursor.execute("SELECT NIF FROM Cliente WHERE NIF = ?", (cliente_nif,))
        if not cursor.fetchone():
            return render_template('equipamento_form.html', clientes=clientes,
                                   erro=f"Cliente com NIF {cliente_nif} nao encontrado.")

        try:
            # Inserir equipamento e associa-lo ao cliente na mesma transacao
            cursor.execute("INSERT INTO Equipamento (NumeroSerie) VALUES (?)", (numero_serie,))
            equip_id = cursor.lastrowid
            cursor.execute('''
                INSERT INTO PropriedadeEquipamento (Equipamento_ID, Cliente_NIF)
                VALUES (?, ?)
            ''', (equip_id, cliente_nif))
            db.commit()
            return redirect(url_for('listar_equipamentos'))
        except sqlite3.IntegrityError:
            return render_template('equipamento_form.html', clientes=clientes,
                                   erro=f"Numero de serie '{numero_serie}' ja existe.")

    cursor.execute("SELECT NIF, Nome FROM Cliente")
    clientes = cursor.fetchall()
    return render_template('equipamento_form.html', clientes=clientes)

@app.route('/listar_clientes')
def listar_clientes():
    """Pagina de listagem de todos os clientes."""
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT * FROM Cliente")
    clientes = cursor.fetchall()
    return render_template('listar_clientes.html', clientes=clientes)

@app.route('/listar_equipamentos')
def listar_equipamentos():
    """Pagina de listagem de equipamentos com o cliente proprietario atual."""
    db = get_db()
    cursor = db.cursor()
    cursor.execute('''
        SELECT e.ID, e.NumeroSerie, c.NIF, c.Nome
        FROM Equipamento e
        LEFT JOIN PropriedadeEquipamento pe
               ON e.ID = pe.Equipamento_ID AND pe.DataFim IS NULL
        LEFT JOIN Cliente c ON pe.Cliente_NIF = c.NIF
        ORDER BY e.ID
    ''')
    equipamentos = cursor.fetchall()
    return render_template('listar_equipamentos.html', equipamentos=equipamentos)

@app.route('/listar_temperaturas')
def listar_temperaturas():
    """Pagina de listagem de temperaturas com paginacao (12 por pagina)."""
    db = get_db()
    cursor = db.cursor()

    page     = request.args.get('page', 1, type=int)
    per_page = 12
    offset   = (page - 1) * per_page

    cursor.execute('SELECT COUNT(*) FROM Temperatura')
    total       = cursor.fetchone()[0]
    total_pages = max(1, (total + per_page - 1) // per_page)

    cursor.execute('''
        SELECT * FROM Temperatura ORDER BY DataHora DESC LIMIT ? OFFSET ?
    ''', (per_page, offset))
    temperaturas = cursor.fetchall()

    return render_template('listar_temperaturas.html',
                           temperaturas=temperaturas,
                           page=page,
                           total_pages=total_pages)

# ---------------------------------------------------------------------------
# API REST - Clientes
# ---------------------------------------------------------------------------

@app.route('/api/clientes', methods=['GET'])
def api_listar_clientes():
    """
    Listar todos os clientes
    ---
    tags:
      - Clientes
    responses:
      200:
        description: Lista de clientes em formato JSON
    """
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT * FROM Cliente")
    return jsonify([dict(row) for row in cursor.fetchall()]), 200


@app.route('/api/clientes', methods=['POST'])
@verificar_token
def api_criar_cliente():
    """
    Criar novo cliente (requer JWT)
    ---
    tags:
      - Clientes
    security:
      - Bearer: []
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [NIF, Nome]
          properties:
            NIF:
              type: string
              example: "123456789"
            Nome:
              type: string
              example: "Empresa ABC Lda"
            Morada:
              type: string
            CodigoPostal:
              type: string
            Localidade:
              type: string
            Area:
              type: string
            Zona:
              type: string
    responses:
      201:
        description: Cliente criado com sucesso
      400:
        description: Campos obrigatorios em falta ou JSON invalido
      401:
        description: Nao autorizado
      409:
        description: NIF ja existe
    """
    data = request.get_json()
    if not data:
        return jsonify({'erro': 'Corpo JSON em falta ou malformado'}), 400

    nif  = data.get('NIF', '').strip()
    nome = data.get('Nome', '').strip()
    if not nif or not nome:
        return jsonify({'erro': 'Campos obrigatorios em falta: NIF, Nome'}), 400

    db = get_db()
    cursor = db.cursor()
    try:
        cursor.execute('''
            INSERT INTO Cliente (NIF, Nome, Morada, CodigoPostal, Localidade, Area, Zona)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (nif, nome,
              data.get('Morada', ''),
              data.get('CodigoPostal', ''),
              data.get('Localidade', ''),
              data.get('Area', ''),
              data.get('Zona', '')))
        db.commit()
        return jsonify({'mensagem': 'Cliente criado com sucesso', 'NIF': nif}), 201
    except sqlite3.IntegrityError:
        return jsonify({'erro': f'Ja existe um cliente com o NIF {nif}'}), 409


@app.route('/api/clientes/<nif>', methods=['GET'])
def api_obter_cliente(nif):
    """
    Obter cliente por NIF
    ---
    tags:
      - Clientes
    parameters:
      - in: path
        name: nif
        required: true
        type: string
    responses:
      200:
        description: Dados do cliente
      404:
        description: Cliente nao encontrado
    """
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT * FROM Cliente WHERE NIF = ?", (nif,))
    row = cursor.fetchone()
    if not row:
        return jsonify({'erro': f'Cliente com NIF {nif} nao encontrado'}), 404
    return jsonify(dict(row)), 200

# ---------------------------------------------------------------------------
# API REST - Equipamentos
# ---------------------------------------------------------------------------

@app.route('/api/equipamentos', methods=['GET'])
def api_listar_equipamentos():
    """
    Listar todos os equipamentos com cliente proprietario atual
    ---
    tags:
      - Equipamentos
    responses:
      200:
        description: Lista de equipamentos
    """
    db = get_db()
    cursor = db.cursor()
    cursor.execute('''
        SELECT e.ID, e.NumeroSerie, c.NIF AS Cliente_NIF, c.Nome AS Cliente_Nome
        FROM Equipamento e
        LEFT JOIN PropriedadeEquipamento pe
               ON e.ID = pe.Equipamento_ID AND pe.DataFim IS NULL
        LEFT JOIN Cliente c ON pe.Cliente_NIF = c.NIF
        ORDER BY e.ID
    ''')
    return jsonify([dict(row) for row in cursor.fetchall()]), 200


@app.route('/api/equipamentos', methods=['POST'])
@verificar_token
def api_criar_equipamento():
    """
    Criar novo equipamento e associa-lo a um cliente (requer JWT)
    ---
    tags:
      - Equipamentos
    security:
      - Bearer: []
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [NumeroSerie, Cliente_NIF]
          properties:
            NumeroSerie:
              type: string
              example: "SN-001-2024"
            Cliente_NIF:
              type: string
              example: "123456789"
    responses:
      201:
        description: Equipamento criado com sucesso
      400:
        description: Campos obrigatorios em falta
      401:
        description: Nao autorizado
      404:
        description: Cliente nao encontrado
      409:
        description: Numero de serie ja existe
    """
    data = request.get_json()
    if not data:
        return jsonify({'erro': 'Corpo JSON em falta ou malformado'}), 400

    numero_serie = data.get('NumeroSerie', '').strip()
    cliente_nif  = data.get('Cliente_NIF', '').strip()
    if not numero_serie or not cliente_nif:
        return jsonify({'erro': 'Campos obrigatorios em falta: NumeroSerie, Cliente_NIF'}), 400

    db = get_db()
    cursor = db.cursor()

    # Verificar se o cliente existe antes de criar o equipamento
    cursor.execute("SELECT NIF FROM Cliente WHERE NIF = ?", (cliente_nif,))
    if not cursor.fetchone():
        return jsonify({'erro': f'Cliente com NIF {cliente_nif} nao encontrado'}), 404

    try:
        # Inserir equipamento e registo de propriedade na mesma transacao
        cursor.execute("INSERT INTO Equipamento (NumeroSerie) VALUES (?)", (numero_serie,))
        equip_id = cursor.lastrowid
        cursor.execute('''
            INSERT INTO PropriedadeEquipamento (Equipamento_ID, Cliente_NIF)
            VALUES (?, ?)
        ''', (equip_id, cliente_nif))
        db.commit()
        return jsonify({
            'mensagem': 'Equipamento criado com sucesso',
            'ID': equip_id,
            'NumeroSerie': numero_serie,
            'Cliente_NIF': cliente_nif
        }), 201
    except sqlite3.IntegrityError:
        return jsonify({'erro': f"Numero de serie '{numero_serie}' ja existe"}), 409


@app.route('/api/equipamentos/<int:equip_id>', methods=['GET'])
def api_obter_equipamento(equip_id):
    """
    Obter equipamento por ID
    ---
    tags:
      - Equipamentos
    parameters:
      - in: path
        name: equip_id
        required: true
        type: integer
    responses:
      200:
        description: Dados do equipamento
      404:
        description: Equipamento nao encontrado
    """
    db = get_db()
    cursor = db.cursor()
    cursor.execute('''
        SELECT e.ID, e.NumeroSerie, c.NIF AS Cliente_NIF, c.Nome AS Cliente_Nome
        FROM Equipamento e
        LEFT JOIN PropriedadeEquipamento pe
               ON e.ID = pe.Equipamento_ID AND pe.DataFim IS NULL
        LEFT JOIN Cliente c ON pe.Cliente_NIF = c.NIF
        WHERE e.ID = ?
    ''', (equip_id,))
    row = cursor.fetchone()
    if not row:
        return jsonify({'erro': f'Equipamento com ID {equip_id} nao encontrado'}), 404
    return jsonify(dict(row)), 200


@app.route('/api/equipamentos/<int:equip_id>', methods=['DELETE'])
@verificar_token
def api_remover_equipamento(equip_id):
    """
    Remover equipamento permanentemente (requer JWT)
    ---
    tags:
      - Equipamentos
    security:
      - Bearer: []
    parameters:
      - in: path
        name: equip_id
        required: true
        type: integer
    responses:
      200:
        description: Equipamento removido com sucesso
      401:
        description: Nao autorizado
      404:
        description: Equipamento nao encontrado
    """
    db = get_db()
    cursor = db.cursor()

    cursor.execute("SELECT ID FROM Equipamento WHERE ID = ?", (equip_id,))
    if not cursor.fetchone():
        return jsonify({'erro': f'Equipamento com ID {equip_id} nao encontrado'}), 404

    # ON DELETE CASCADE no schema remove automaticamente registos dependentes
    cursor.execute("DELETE FROM Equipamento WHERE ID = ?", (equip_id,))
    db.commit()

    return jsonify({
        'mensagem': f'Equipamento ID {equip_id} removido com sucesso'
    }), 200


def transferir_propriedade(db, equip_id, novo_nif):
    """Fecha o registo de propriedade aberto e abre um novo para novo_nif.

    O fim do registo anterior e o inicio do novo usam o mesmo instante, em UTC e
    no mesmo formato do CURRENT_TIMESTAMP do SQLite (usado ao criar equipamentos).
    O instante nunca e anterior ao inicio do registo aberto, por isso DataFim >= DataInicio
    mesmo em transferencias consecutivas no mesmo segundo. Deve ser chamada dentro
    de uma transacao. Devolve o instante usado."""
    aberto = db.execute('''
        SELECT DataInicio FROM PropriedadeEquipamento
         WHERE Equipamento_ID = ? AND DataFim IS NULL
         ORDER BY ID DESC LIMIT 1
    ''', (equip_id,)).fetchone()

    instante = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    if aberto and aberto['DataInicio'] and str(aberto['DataInicio']) > instante:
        instante = str(aberto['DataInicio'])

    db.execute('''
        UPDATE PropriedadeEquipamento SET DataFim = ?
         WHERE Equipamento_ID = ? AND DataFim IS NULL
    ''', (instante, equip_id))
    db.execute('''
        INSERT INTO PropriedadeEquipamento (Equipamento_ID, Cliente_NIF, DataInicio)
        VALUES (?, ?, ?)
    ''', (equip_id, novo_nif, instante))
    return instante


@app.route('/api/equipamentos/<int:equip_id>', methods=['PATCH'])
@verificar_token
def api_transferir_equipamento(equip_id):
    """
    Transferir equipamento para outro cliente (requer JWT)
    ---
    tags:
      - Equipamentos
    security:
      - Bearer: []
    parameters:
      - in: path
        name: equip_id
        required: true
        type: integer
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [Cliente_NIF]
          properties:
            Cliente_NIF:
              type: string
              example: "987654321"
    responses:
      200:
        description: Propriedade transferida com sucesso
      400:
        description: Dados invalidos
      401:
        description: Nao autorizado
      404:
        description: Equipamento ou cliente nao encontrado
    """
    data = request.get_json()
    if not data:
        return jsonify({'erro': 'Corpo JSON em falta ou malformado'}), 400

    novo_nif = data.get('Cliente_NIF', '').strip()
    if not novo_nif:
        return jsonify({'erro': 'Campo obrigatorio em falta: Cliente_NIF'}), 400

    db = get_db()
    try:
        # Transacao exclusiva: validacoes e escrita sem interferencia de pedidos concorrentes
        db.execute("BEGIN IMMEDIATE")

        if not db.execute("SELECT ID FROM Equipamento WHERE ID = ?", (equip_id,)).fetchone():
            db.rollback()
            return jsonify({'erro': f'Equipamento com ID {equip_id} nao encontrado'}), 404

        if not db.execute("SELECT NIF FROM Cliente WHERE NIF = ?", (novo_nif,)).fetchone():
            db.rollback()
            return jsonify({'erro': f'Cliente com NIF {novo_nif} nao encontrado'}), 404

        instante = transferir_propriedade(db, equip_id, novo_nif)
        db.commit()
    except Exception:
        db.rollback()
        raise

    return jsonify({
        'mensagem': f'Equipamento ID {equip_id} transferido para cliente NIF {novo_nif}',
        'data_transferencia': instante
    }), 200


@app.route('/api/equipamentos/<int:equip_id>', methods=['PUT'])
@verificar_token
def api_substituir_equipamento(equip_id):
    """
    Substituir equipamento na integra (requer JWT)
    ---
    tags:
      - Equipamentos
    security:
      - Bearer: []
    parameters:
      - in: path
        name: equip_id
        required: true
        type: integer
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [NumeroSerie, Cliente_NIF]
          properties:
            NumeroSerie:
              type: string
            Cliente_NIF:
              type: string
    responses:
      200:
        description: Equipamento atualizado com sucesso
      400:
        description: Dados invalidos
      401:
        description: Nao autorizado
      404:
        description: Equipamento ou cliente nao encontrado
      409:
        description: Numero de serie ja usado por outro equipamento (nada e alterado)
    """
    data = request.get_json()
    if not data:
        return jsonify({'erro': 'Corpo JSON em falta ou malformado'}), 400

    numero_serie = data.get('NumeroSerie', '').strip()
    novo_nif     = data.get('Cliente_NIF', '').strip()
    if not numero_serie or not novo_nif:
        return jsonify({'erro': 'Campos obrigatorios em falta: NumeroSerie, Cliente_NIF'}), 400

    db = get_db()
    try:
        # Transacao exclusiva: ou todas as alteracoes sao gravadas, ou nenhuma
        db.execute("BEGIN IMMEDIATE")

        if not db.execute("SELECT ID FROM Equipamento WHERE ID = ?", (equip_id,)).fetchone():
            db.rollback()
            return jsonify({'erro': f'Equipamento com ID {equip_id} nao encontrado'}), 404

        if not db.execute("SELECT NIF FROM Cliente WHERE NIF = ?", (novo_nif,)).fetchone():
            db.rollback()
            return jsonify({'erro': f'Cliente com NIF {novo_nif} nao encontrado'}), 404

        # O numero de serie e unico: recusar se pertencer a outro equipamento
        if db.execute("SELECT ID FROM Equipamento WHERE NumeroSerie = ? AND ID <> ?",
                      (numero_serie, equip_id)).fetchone():
            db.rollback()
            return jsonify({'erro': f'Numero de serie {numero_serie} ja existe noutro equipamento'}), 409

        db.execute("UPDATE Equipamento SET NumeroSerie = ? WHERE ID = ?",
                   (numero_serie, equip_id))
        transferir_propriedade(db, equip_id, novo_nif)
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        return jsonify({'erro': 'Conflito de dados: o pedido viola uma restricao de integridade'}), 409
    except Exception:
        db.rollback()
        raise

    return jsonify({
        'mensagem': f'Equipamento ID {equip_id} atualizado com sucesso',
        'NumeroSerie': numero_serie,
        'Cliente_NIF': novo_nif
    }), 200

# ---------------------------------------------------------------------------
# Rota de rececao de temperaturas - compativel com temp_rand_main.py
# ---------------------------------------------------------------------------

@app.route('/temperatura', methods=['POST', 'GET'])
@verificar_token
def handle_temperatura():
    """
    Receber leitura de temperatura de um equipamento (requer JWT)
    ---
    tags:
      - Temperaturas
    security:
      - Bearer: []
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [equipamento_id, datahora, temp0, temp1, temp2]
          properties:
            equipamento_id:
              type: string
              example: "1"
            datahora:
              type: string
              example: "2024-01-15 10:30:00"
            temp0:
              type: number
              example: -10.5
            temp1:
              type: number
              example: -15.2
            temp2:
              type: number
              example: 22.1
    responses:
      200:
        description: Dados inseridos com sucesso
      400:
        description: Formato de dados invalido ou campos em falta
      401:
        description: Nao autorizado
      404:
        description: Equipamento nao encontrado
    """
    if request.method == 'POST':
        # Aceitar JSON (formato principal, alinea D) ou CSV (compatibilidade com repo base)
        data_json = request.get_json(silent=True)
        if data_json:
            return handle_data_json(data_json)
        else:
            # Fallback CSV para compatibilidade com versoes antigas do script de sensor
            return handle_data_csv(request.data.decode('utf-8'))
    else:
        return handle_data_csv(request.args.get('data', ''))


def handle_data_json(data):
    """Processa leitura de temperatura recebida em formato JSON (alinea D)."""
    campos = ['equipamento_id', 'datahora', 'temp0', 'temp1', 'temp2']
    for campo in campos:
        if campo not in data:
            return jsonify({'erro': f'Campo obrigatorio em falta: {campo}'}), 400

    try:
        equipment_id = str(data['equipamento_id']).strip()
        current_time = str(data['datahora']).strip()
        temp0 = float(data['temp0'])
        temp1 = float(data['temp1'])
        temp2 = float(data['temp2'])
    except (ValueError, TypeError):
        return jsonify({'erro': 'Valores de temperatura invalidos (devem ser numeros)'}), 400

    return persistir_temperatura(equipment_id, current_time, temp0, temp1, temp2)


def handle_data_csv(input_data):
    """Processa leitura de temperatura recebida em formato CSV (compatibilidade)."""
    partes = input_data.strip().split(',')
    if len(partes) != 5:
        return jsonify({'erro': 'Formato invalido. Esperado JSON ou CSV: ID,DataHora,Temp0,Temp1,Temp2'}), 400

    equipment_id, current_time, raw0, raw1, raw2 = partes
    try:
        temp0 = float(raw0)
        temp1 = float(raw1)
        temp2 = float(raw2)
    except ValueError:
        return jsonify({'erro': 'Valores de temperatura invalidos (devem ser numeros)'}), 400

    return persistir_temperatura(equipment_id.strip(), current_time.strip(), temp0, temp1, temp2)


def persistir_temperatura(equipment_id, current_time, temp0, temp1, temp2):
    """Valida o equipamento, persiste a leitura e dispara verificacao de alertas."""
    db = get_db()
    cursor = db.cursor()

    # Garantir que o equipamento existe antes de inserir a leitura
    cursor.execute("SELECT ID FROM Equipamento WHERE ID = ?", (equipment_id,))
    if not cursor.fetchone():
        return jsonify({'erro': f'Equipamento ID {equipment_id} nao encontrado'}), 404

    cursor.execute('''
        INSERT INTO Temperatura (Equipamento_ID, DataHora, Temp0, Temp1, Temp2)
        VALUES (?, ?, ?, ?, ?)
    ''', (equipment_id, current_time, temp0, temp1, temp2))
    db.commit()

    # Verificar alertas em thread separada (nao bloqueia a resposta HTTP)
    verificar_alertas_async(equipment_id, temp0, temp1, temp2, current_time)

    # Notificar clientes ligados via WebSocket
    notifica_atualiza_temperaturas()

    return jsonify({'mensagem': 'Dados inseridos com sucesso.'}), 200

# ---------------------------------------------------------------------------
# API REST - Alertas
# ---------------------------------------------------------------------------

@app.route('/api/alertas', methods=['GET'])
@verificar_token
def api_listar_alertas():
    """
    Obter historico completo de alertas de sensores (requer JWT)
    ---
    tags:
      - Alertas
    security:
      - Bearer: []
    responses:
      200:
        description: Lista de alertas registados
      401:
        description: Nao autorizado
    """
    db = get_db()
    cursor = db.cursor()
    # Juntar alertas com dados do equipamento e limites configurados para contexto completo
    cursor.execute('''
        SELECT a.id, a.Equipamento_ID, e.NumeroSerie, a.Sensor_tipo,
               a.Leitura, a.DataHora, a.Mensagem,
               l.Min_val, l.Max_val
          FROM alertas_sensores a
          JOIN Equipamento e      ON a.Equipamento_ID = e.ID
          JOIN limites_sensores l ON a.Sensor_tipo    = l.Sensor_tipo
         ORDER BY a.DataHora DESC
    ''')
    return jsonify([dict(row) for row in cursor.fetchall()]), 200


@app.route('/api/alertas/<int:equip_id>', methods=['GET'])
@verificar_token
def api_alertas_equipamento(equip_id):
    """
    Obter alertas de um equipamento especifico (requer JWT)
    ---
    tags:
      - Alertas
    security:
      - Bearer: []
    parameters:
      - in: path
        name: equip_id
        required: true
        type: integer
    responses:
      200:
        description: Lista de alertas do equipamento
      401:
        description: Nao autorizado
      404:
        description: Equipamento nao encontrado
    """
    db = get_db()
    cursor = db.cursor()

    cursor.execute("SELECT ID FROM Equipamento WHERE ID = ?", (equip_id,))
    if not cursor.fetchone():
        return jsonify({'erro': f'Equipamento com ID {equip_id} nao encontrado'}), 404

    cursor.execute('''
        SELECT a.id, a.Equipamento_ID, a.Sensor_tipo,
               a.Leitura, a.DataHora, a.Mensagem
          FROM alertas_sensores a
         WHERE a.Equipamento_ID = ?
         ORDER BY a.DataHora DESC
    ''', (equip_id,))
    return jsonify([dict(row) for row in cursor.fetchall()]), 200

# ---------------------------------------------------------------------------
# WebSocket - notificacao em tempo real das temperaturas
# ---------------------------------------------------------------------------

def notifica_atualiza_temperaturas(page=1):
    """Emite evento WebSocket com as leituras de temperatura mais recentes,
    para atualizacao da interface sem recarregar a pagina."""
    db = get_db()
    cursor = db.cursor()
    per_page = 12
    offset   = (page - 1) * per_page

    cursor.execute("SELECT COUNT(*) FROM Temperatura")
    total       = cursor.fetchone()[0]
    total_pages = max(1, (total + per_page - 1) // per_page)

    cursor.execute('''
        SELECT * FROM Temperatura ORDER BY DataHora DESC LIMIT ? OFFSET ?
    ''', (per_page, offset))
    temperaturas = [tuple(row) for row in cursor.fetchall()]

    socketio.emit('atualiza_temperaturas', {
        'temperaturas': temperaturas,
        'page': page,
        'total_pages': total_pages
    })

# ---------------------------------------------------------------------------
# Rotas de compatibilidade com o cliente original do repositorio base
# ---------------------------------------------------------------------------

@app.route('/equipamentos_cliente/json', methods=['POST'])
def equipamentos_cliente_json():
    """Lista equipamentos de um cliente especifico (compatibilidade com cliente original)."""
    db = get_db()
    cursor = db.cursor()
    data = request.get_json()
    cliente_nif = data.get('cliente_nif') if data else None
    if not cliente_nif:
        return jsonify({'erro': 'Necessario o parametro cliente_nif no corpo JSON'}), 400

    cursor.execute('''
        SELECT e.ID, e.NumeroSerie, pe.Cliente_NIF
          FROM Equipamento e
          JOIN PropriedadeEquipamento pe
               ON e.ID = pe.Equipamento_ID AND pe.DataFim IS NULL
         WHERE pe.Cliente_NIF = ?
    ''', (cliente_nif,))
    resultado = [{'id': r[0], 'numero_serie': r[1], 'cliente_nif': r[2]}
                 for r in cursor.fetchall()]
    return jsonify(resultado), 200


@app.route('/listar_equipamentos/json')
def listar_equipamentos_json():
    """Lista todos os equipamentos em JSON (compatibilidade com cliente original)."""
    db = get_db()
    cursor = db.cursor()
    cursor.execute('''
        SELECT e.ID, e.NumeroSerie, pe.Cliente_NIF
          FROM Equipamento e
          LEFT JOIN PropriedadeEquipamento pe
               ON e.ID = pe.Equipamento_ID AND pe.DataFim IS NULL
    ''')
    resultado = [{'id': r[0], 'numero_serie': r[1], 'cliente_nif': r[2]}
                 for r in cursor.fetchall()]
    return jsonify(resultado), 200

# ---------------------------------------------------------------------------
# Ponto de entrada
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Criar base de dados automaticamente se ainda nao existir
    if not os.path.exists(DATABASE):
        init_db()

    print(f"[INFO] Servidor iniciado em http://{host}:{port}")
    print(f"[INFO] Documentacao API: http://localhost:{port}/api/docs")
    if os.environ.get('MONITOR_ADMIN_PASSWORD'):
        print(f"[INFO] Utilizador: {ADMIN_USER} (password definida em MONITOR_ADMIN_PASSWORD)")
    else:
        print(f"[INFO] Credenciais geradas para esta sessao: {ADMIN_USER} / {ADMIN_PASSWORD}")
    socketio.run(app, host=host, port=port, debug=debug, allow_unsafe_werkzeug=True)
